"""回測盈虧彙總：獲利因子、已實現/未平倉/總盈虧（USDT）。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import config as cfg


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
    closed_pnls = [hunting_closed_pnl_usdt(t) for t in trades if t.result != "OPEN"]
    realized = sum(closed_pnls)
    unrealized = 0.0
    for leg in open_legs:
        r_part, u_part = hunting_open_leg_pnl_usdt(leg, last_close)
        realized += r_part
        unrealized += u_part
    return PnLSummary(
        profit_factor=profit_factor_from_pnls(closed_pnls),
        realized_pnl_usdt=realized,
        unrealized_pnl_usdt=unrealized,
        total_pnl_usdt=realized + unrealized,
    )
