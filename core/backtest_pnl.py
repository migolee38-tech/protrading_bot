"""回測盈虧彙總：獲利因子、已實現/未平倉/總盈虧（USDT）。"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import config as cfg

if TYPE_CHECKING:
    from engine import TradingEngine
    from position import Position
    from position_donchian import DonchianPosition


@dataclass
class PnLSummary:
    profit_factor: float
    realized_pnl_usdt: float
    unrealized_pnl_usdt: float
    total_pnl_usdt: float

    def to_dict(self) -> dict[str, float]:
        pf = self.profit_factor
        return {
            "profit_factor": round(pf, 4) if math.isfinite(pf) else 9999.0,
            "realized_pnl_usdt": round(self.realized_pnl_usdt, 4),
            "unrealized_pnl_usdt": round(self.unrealized_pnl_usdt, 4),
            "total_pnl_usdt": round(self.total_pnl_usdt, 4),
        }


def pnl_usdt(side: str, entry: float, exit_px: float, qty: float) -> float:
    if qty <= 0 or entry <= 0:
        return 0.0
    if side == "long":
        return (exit_px - entry) * qty
    return (entry - exit_px) * qty


def profit_factor_from_pnls(closed_pnls: list[float]) -> float:
    gross_profit = sum(p for p in closed_pnls if p > 0)
    gross_loss = abs(sum(p for p in closed_pnls if p < 0))
    if gross_loss <= 0:
        return float("inf") if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


@dataclass
class BacktestLedger:
    """引擎回測：逐筆平倉事件累計已實現盈虧。"""

    realized_usdt: float = 0.0
    closed_trade_pnls: list[float] = field(default_factory=list)
    _open_trade_pnl: float = 0.0

    def on_position_opened(self) -> None:
        self._open_trade_pnl = 0.0

    def record_delta(self, delta: float, position_closed: bool) -> None:
        self.realized_usdt += delta
        self._open_trade_pnl += delta
        if position_closed:
            self.closed_trade_pnls.append(self._open_trade_pnl)
            self._open_trade_pnl = 0.0

    def summary(self, unrealized_usdt: float) -> PnLSummary:
        pf = profit_factor_from_pnls(self.closed_trade_pnls)
        return PnLSummary(
            profit_factor=pf,
            realized_pnl_usdt=self.realized_usdt,
            unrealized_pnl_usdt=unrealized_usdt,
            total_pnl_usdt=self.realized_usdt + unrealized_usdt,
        )


def _pnl_from_standard_events(
    pos: Position,
    size_at_bar_start: float,
    events: list[str],
) -> float:
    p = pos.plan
    delta = 0.0
    running = size_at_bar_start
    init = pos.initial_size
    for ev in events:
        if ev.startswith("partial_tp_1r"):
            q = init * cfg.REDUCE_AT_1R_PCT
            delta += pnl_usdt(p.side, p.entry, p.tp_1r, q)
            running = max(0.0, running - q)
        elif ev.startswith("partial_tp_2r"):
            q = init * cfg.REDUCE_AT_2R_PCT
            delta += pnl_usdt(p.side, p.entry, p.tp_2r, q)
            running = max(0.0, running - q)
        elif ev == "final_tp_10r":
            delta += pnl_usdt(p.side, p.entry, p.tp_final, running)
            running = 0.0
        elif ev == "stop_loss":
            delta += pnl_usdt(p.side, p.entry, pos.stop, running)
            running = 0.0
    return delta


def _pnl_from_donchian_events(
    pos: DonchianPosition,
    size_at_bar_start: float,
    events: list[str],
) -> float:
    p = pos.plan
    delta = 0.0
    running = size_at_bar_start
    init = pos.initial_size
    for ev in events:
        if ev.startswith("partial_tp_2r"):
            q = init * cfg.DONCHIAN_REDUCE_TP1_PCT
            delta += pnl_usdt(p.side, p.entry, p.tp_1r, q)
            running = max(0.0, running - q)
        elif ev.startswith("partial_tp_5r"):
            q = running * cfg.DONCHIAN_REDUCE_TP2_PCT
            delta += pnl_usdt(p.side, p.entry, p.tp_2r, q)
            running = max(0.0, running - q)
        elif ev == "final_tp_10r":
            delta += pnl_usdt(p.side, p.entry, p.tp_final, running)
            running = 0.0
        elif ev == "stop_loss":
            delta += pnl_usdt(p.side, p.entry, pos.stop, running)
            running = 0.0
    return delta


def record_position_bar_pnl(
    ledger: BacktestLedger,
    pos: Any,
    size_before: float,
    events: list[str],
) -> None:
    if cfg.STRATEGY == "donchian":
        delta = _pnl_from_donchian_events(pos, size_before, events)
    else:
        delta = _pnl_from_standard_events(pos, size_before, events)
    ledger.record_delta(delta, position_closed=bool(pos.closed))


def unrealized_engine_usdt(pos: Any, last_close: float) -> float:
    if pos is None or pos.closed or pos.size <= 0:
        return 0.0
    return pnl_usdt(pos.plan.side, pos.plan.entry, last_close, pos.size)


def summarize_engine_pnl(engine: TradingEngine, df: Any) -> PnLSummary:
    ledger: BacktestLedger | None = getattr(engine, "pnl_ledger", None)
    if ledger is None:
        return PnLSummary(0.0, 0.0, 0.0, 0.0)
    last_close = float(df.iloc[-1]["close"])
    pos = engine.portfolio.get(engine.symbol)
    unrealized = unrealized_engine_usdt(pos, last_close)
    return ledger.summary(unrealized)


def _hunting_margin_per_leg() -> float:
    return cfg.HUNTING_TOTAL_CAPITAL * cfg.HUNTING_POSITION_PCT / 100.0


def hunting_closed_pnl_usdt(trade: Any) -> float:
    margin = _hunting_margin_per_leg()
    risk = abs(trade.entry_price - trade.sl)
    qty = margin / trade.entry_price if trade.entry_price else 0.0
    return trade.pnl_r * risk * qty


def hunting_open_leg_pnl_usdt(leg: Any, last_close: float) -> tuple[float, float]:
    """回傳 (已實現 USDT, 未平倉 USDT)。"""
    margin = _hunting_margin_per_leg()
    qty = margin / leg.entry_price if leg.entry_price else 0.0
    risk = abs(leg.entry_price - leg.initial_sl)
    realized = leg.realized_r * risk * qty
    if leg.direction == "LONG":
        unrealized = (last_close - leg.entry_price) * qty * leg.remaining
    else:
        unrealized = (leg.entry_price - last_close) * qty * leg.remaining
    return realized, unrealized


def summarize_hunting_pnl(
    trades: list[Any],
    open_legs: list[Any],
    last_close: float,
) -> PnLSummary:
    closed = [t for t in trades if t.result != "OPEN"]
    closed_pnls = [hunting_closed_pnl_usdt(t) for t in closed]
    realized_closed = sum(closed_pnls)

    open_realized = 0.0
    unrealized = 0.0
    for leg in open_legs:
        r, u = hunting_open_leg_pnl_usdt(leg, last_close)
        open_realized += r
        unrealized += u

    realized_total = realized_closed + open_realized
    return PnLSummary(
        profit_factor=profit_factor_from_pnls(closed_pnls),
        realized_pnl_usdt=realized_total,
        unrealized_pnl_usdt=unrealized,
        total_pnl_usdt=realized_total + unrealized,
    )
