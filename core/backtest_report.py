"""回測報告：勝率、事件統計、匯出。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import pandas as pd

import config as cfg
from core.strategy_context import use_strategy
from core.strategy_registry import StrategyMeta, get_strategy
from engine import TradingEngine
from indicators import min_bars_required


@dataclass
class BacktestResult:
    strategy_id: str
    symbol: str
    timeframe: str
    bars: int
    signal_count: int
    open_count: int
    win_count: int
    loss_count: int
    win_rate: float
    profit_factor: float = 0.0
    realized_pnl_usdt: float = 0.0
    unrealized_pnl_usdt: float = 0.0
    total_pnl_usdt: float = 0.0
    events: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        pf = self.profit_factor
        return {
            "strategy": self.strategy_id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "bars": self.bars,
            "signals": self.signal_count,
            "trades_opened": self.open_count,
            "wins": self.win_count,
            "losses": self.loss_count,
            "win_rate_pct": round(self.win_rate * 100, 2),
            "profit_factor": round(pf, 4) if pf != float("inf") else 9999.0,
            "realized_pnl_usdt": round(self.realized_pnl_usdt, 4),
            "unrealized_pnl_usdt": round(self.unrealized_pnl_usdt, 4),
            "total_pnl_usdt": round(self.total_pnl_usdt, 4),
        }


_OPEN_RE = re.compile(r"\[(\d+)\] open (long|short)")
_WIN_RE = re.compile(
    r"final_tp|tp_1r|tp_2r|tp_5r|tp_10r|breakeven|trail"
)
_LOSS_RE = re.compile(r"stop_loss")


def _parse_stats(log_entries: list[str]) -> tuple[int, int, int]:
    opens = wins = losses = 0
    for line in log_entries:
        if _OPEN_RE.search(line):
            opens += 1
        if _LOSS_RE.search(line):
            losses += 1
        elif _WIN_RE.search(line) and "open" not in line:
            wins += 1
    return opens, wins, losses


def _run_hunting_backtest(
    strategy_id: str,
    symbol: str,
    raw_df: pd.DataFrame,
    meta: StrategyMeta,
) -> BacktestResult:
    from core.strategy_registry import with_symbol
    from strategies.hunting_funding import run_dashboard_backtest

    df = meta.prepare_df(with_symbol(raw_df, symbol))
    with use_strategy(strategy_id):
        need = min_bars_required()
        if len(df) < need:
            return BacktestResult(
                strategy_id=strategy_id,
                symbol=symbol,
                timeframe=meta.timeframe,
                bars=len(df),
                signal_count=0,
                open_count=0,
                win_count=0,
                loss_count=0,
                win_rate=0.0,
                events=[f"K 線不足，需要至少 {need} 根"],
            )
        stats = run_dashboard_backtest(df)
    return BacktestResult(
        strategy_id=strategy_id,
        symbol=symbol,
        timeframe=meta.timeframe,
        bars=len(df),
        signal_count=stats["signal_count"],
        open_count=stats["open_count"],
        win_count=stats["wins"],
        loss_count=stats["losses"],
        win_rate=stats["win_rate"],
        profit_factor=stats.get("profit_factor", 0.0),
        realized_pnl_usdt=stats.get("realized_pnl_usdt", 0.0),
        unrealized_pnl_usdt=stats.get("unrealized_pnl_usdt", 0.0),
        total_pnl_usdt=stats.get("total_pnl_usdt", 0.0),
        events=stats["events"],
    )


def run_backtest(
    strategy_id: str,
    symbol: str,
    raw_df: pd.DataFrame,
    meta: StrategyMeta | None = None,
) -> BacktestResult:
    meta = meta or get_strategy(strategy_id)
    if strategy_id == "hunting_funding":
        return _run_hunting_backtest(strategy_id, symbol, raw_df, meta)

    from core.strategy_registry import with_symbol

    df = meta.prepare_df(with_symbol(raw_df, symbol))

    with use_strategy(strategy_id):
        need = min_bars_required()
        if len(df) < need:
            return BacktestResult(
                strategy_id=strategy_id,
                symbol=symbol,
                timeframe=meta.timeframe,
                bars=len(df),
                signal_count=0,
                open_count=0,
                win_count=0,
                loss_count=0,
                win_rate=0.0,
                events=[f"K 線不足，需要至少 {need} 根"],
            )

        from core.backtest_pnl import BacktestLedger, summarize_engine_pnl
        from core.strategy_registry import scan_signals_for

        signals = scan_signals_for(strategy_id, df)
        tf_min = cfg.timeframe_minutes(meta.timeframe)
        engine = TradingEngine(symbol=symbol, bar_minutes=tf_min)
        engine.pnl_ledger = BacktestLedger()
        log = engine.run(df)
        pnl = summarize_engine_pnl(engine, df)

    opens, wins, losses = _parse_stats(log.entries)
    closed = wins + losses
    win_rate = (wins / closed) if closed > 0 else 0.0

    return BacktestResult(
        strategy_id=strategy_id,
        symbol=symbol,
        timeframe=meta.timeframe,
        bars=len(df),
        signal_count=len(signals),
        open_count=opens,
        win_count=wins,
        loss_count=losses,
        win_rate=win_rate,
        profit_factor=pnl.profit_factor,
        realized_pnl_usdt=pnl.realized_pnl_usdt,
        unrealized_pnl_usdt=pnl.unrealized_pnl_usdt,
        total_pnl_usdt=pnl.total_pnl_usdt,
        events=log.entries,
    )


def batch_backtest(
    strategy_ids: list[str],
    symbol: str,
    raw_df: pd.DataFrame,
) -> pd.DataFrame:
    rows = []
    for sid in strategy_ids:
        r = run_backtest(sid, symbol, raw_df)
        rows.append(r.to_dict())
    return pd.DataFrame(rows)
