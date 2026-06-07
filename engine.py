"""
逐根 K 線回測引擎：單幣單倉、冷卻、完整出場規則。
EMA 與唐奇安各自獨立進場邏輯，共用持倉管理與冷卻。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

import config as cfg
from cooldown import CooldownManager
from indicators import min_bars_required
from portfolio import Portfolio
from strategies.donchian_multi_tp import (
    PendingEntry,
    Signal as DonchianSignal,
    create_pending,
    is_pending_expired,
    try_fill_pending,
)
from strategies.ema_trend_cross import Signal as EmaSignal
from strategies.ema_trend_cross import evaluate_bar as ema_evaluate_bar
from strategies.macd_momentum import evaluate_bar as macd_evaluate_bar
from strategies.rsi_reversal import evaluate_bar as rsi_evaluate_bar


@dataclass
class EngineLog:
    entries: list[str] = field(default_factory=list)

    def add(self, msg: str) -> None:
        self.entries.append(msg)


@dataclass
class TradingEngine:
    symbol: str
    portfolio: Portfolio = field(default_factory=Portfolio)
    cooldown: CooldownManager = field(default_factory=CooldownManager)
    log: EngineLog = field(default_factory=EngineLog)
    bar_minutes: int = field(default_factory=cfg.timeframe_minutes)
    _pending: Any = field(default=None, init=False, repr=False)
    pnl_ledger: Any = field(default=None, repr=False)

    def _bar_time(self, bar_index: int) -> datetime:
        base = datetime(2020, 1, 1, tzinfo=timezone.utc)
        return base + timedelta(minutes=self.bar_minutes * bar_index)

    def _allowed_side(self, side: str) -> bool:
        if cfg.ALLOWED_SIDE is None:
            return True
        return side == cfg.ALLOWED_SIDE

    def _manage_open_position(self, i: int, high: float, low: float, t: datetime) -> bool:
        """持倉管理；若仍有倉或未平倉事件則回傳 True（跳過進場）。"""
        pos = self.portfolio.get(self.symbol)
        if not pos or pos.closed or pos.size <= 0:
            return False

        size_before = pos.size
        events = pos.on_bar(high, low)
        if self.pnl_ledger is not None:
            from core.backtest_pnl import record_position_bar_pnl

            record_position_bar_pnl(self.pnl_ledger, pos, size_before, events)
        for ev in events:
            self.log.add(f"[{i}] {ev}")
        if "stop_loss" in events:
            triggered = self.cooldown.record_stop_loss(self.symbol, t)
            self.portfolio.close_symbol(self.symbol)
            if triggered:
                self.log.add(f"[{i}] cooldown_24h {self.symbol}")
        elif pos.is_win_exit(events):
            self.cooldown.record_win(self.symbol)
        return True

    # --- 唐奇安專用 ---

    def _donchian_process_pending(self, df: pd.DataFrame, i: int) -> None:
        pending: PendingEntry | None = self._pending
        if pending is None:
            return

        if is_pending_expired(i, pending):
            self.log.add(
                f"[{i}] pending_expired {pending.side} "
                f"limit={pending.limit_price:.4f} signal_bar={pending.signal_bar}"
            )
            self._pending = None
            return

        sig = try_fill_pending(df, i, pending)
        if sig is None:
            return

        self._pending = None
        if not self._allowed_side(sig.side):
            return
        if not self.portfolio.can_open(self.symbol, self.cooldown):
            self.log.add(f"[{i}] pending_fill_skipped (cooldown or已有倉)")
            return

        self._open_donchian(sig, i)

    def _donchian_try_new_pending(self, df: pd.DataFrame, i: int) -> None:
        if self._pending is not None:
            return
        pending = create_pending(df, i)
        if pending is None:
            return
        self._pending = pending
        self.log.add(
            f"[{i}] pending {pending.side} limit={pending.limit_price:.4f} "
            f"expire_bar={pending.expires_at}"
        )

    def _run_donchian(self, df: pd.DataFrame, start: int) -> None:
        self._pending = None
        for i in range(start, len(df)):
            row = df.iloc[i]
            high, low = float(row["high"]), float(row["low"])
            t = self._bar_time(i)

            if self._manage_open_position(i, high, low, t):
                continue

            self._donchian_process_pending(df, i)
            if self.portfolio.has_position(self.symbol):
                continue
            if not self.portfolio.can_open(self.symbol, self.cooldown):
                continue
            self._donchian_try_new_pending(df, i)

    # --- EMA 專用 ---

    def _run_ema(self, df: pd.DataFrame, start: int) -> None:
        self._run_instant_entry(df, start, ema_evaluate_bar)

    def _run_instant_entry(self, df: pd.DataFrame, start: int, evaluate_fn) -> None:
        for i in range(start, len(df)):
            row = df.iloc[i]
            high, low = float(row["high"]), float(row["low"])
            t = self._bar_time(i)

            if self._manage_open_position(i, high, low, t):
                continue
            if not self.portfolio.can_open(self.symbol, self.cooldown):
                continue

            sig = evaluate_fn(df, i)
            if sig is None or not self._allowed_side(sig.side):
                continue

            self._open_ema(sig, i)

    def run(self, df: pd.DataFrame) -> EngineLog:
        start = min_bars_required()
        if cfg.STRATEGY == "donchian":
            self._run_donchian(df, start)
        elif cfg.STRATEGY == "rsi":
            self._run_instant_entry(df, start, rsi_evaluate_bar)
        elif cfg.STRATEGY == "macd":
            self._run_instant_entry(df, start, macd_evaluate_bar)
        else:
            self._run_ema(df, start)
        return self.log

    def _open_ema(self, sig: EmaSignal, bar_index: int) -> None:
        p = sig.plan
        self.portfolio.open_position(self.symbol, p)
        if self.pnl_ledger is not None:
            self.pnl_ledger.on_position_opened()
        self.log.add(
            f"[{bar_index}] open {sig.side} entry={p.entry:.4f} "
            f"stop={p.stop:.4f} tp_1r={p.tp_1r:.4f} tp_2r={p.tp_2r:.4f} "
            f"tp_final={p.tp_final:.4f}"
        )

    def _open_donchian(self, sig: DonchianSignal, bar_index: int) -> None:
        p = sig.plan
        self.portfolio.open_position(self.symbol, p)
        if self.pnl_ledger is not None:
            self.pnl_ledger.on_position_opened()
        self.log.add(
            f"[{bar_index}] open {sig.side} entry={p.entry:.4f} "
            f"stop={p.stop:.4f} tp_2r={p.tp_1r:.4f} tp_5r={p.tp_2r:.4f} "
            f"tp_10r={p.tp_final:.4f} size={p.position_size:.6f} "
            f"risk_usdt={cfg.DONCHIAN_RISK_USDT} signal_bar={sig.signal_bar}"
        )
