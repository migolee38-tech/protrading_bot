"""止損、止盈與 R 計算。"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

import config as cfg


@dataclass(frozen=True)
class TradePlan:
    side: str
    entry: float
    stop: float
    r: float
    tp_1r: float
    tp_2r: float
    tp_final: float
    stop_source: str
    risk_pct: float

    position_size: float = 1.0

    @property
    def stop_1r(self) -> float:
        if self.side == "long":
            return self.entry + self.r
        return self.entry - self.r

    @property
    def stop_3r(self) -> float:
        if self.side == "long":
            return self.entry + 3.0 * self.r
        return self.entry - 3.0 * self.r


def _risk_pct(entry: float, stop: float, side: str) -> float:
    if side == "long":
        return (entry - stop) / entry
    return (stop - entry) / entry


def _apply_buffer(price: float, side: str) -> float:
    if side == "long":
        return price * (1 - cfg.STOP_BUFFER_PCT)
    return price * (1 + cfg.STOP_BUFFER_PCT)


def calc_stop(
    entry: float,
    side: str,
    prev_low: float,
    prev_high: float,
    ema30: float,
    ema55: float,
) -> tuple[float, str] | None:
    """
    止損：前 48 根低/高 + 1% 預留。
    風險 > 12% → 改 EMA30；仍 > 12% → 改 EMA55。
    若仍 > 20% → 不進場。
    """
    if side == "long":
        raw_levels = [
            (prev_low, "prev_48"),
            (ema30, "ema30"),
            (ema55, "ema55"),
        ]
    else:
        raw_levels = [
            (prev_high, "prev_48"),
            (ema30, "ema30"),
            (ema55, "ema55"),
        ]

    chosen: tuple[float, str] | None = None
    for raw, source in raw_levels:
        stop = _apply_buffer(raw, side)
        risk = _risk_pct(entry, stop, side)
        if risk > cfg.MAX_STOP_PCT:
            continue
        chosen = (stop, source)
        if risk <= cfg.SOFT_STOP_PCT:
            break

    return chosen


def build_trade_plan(
    side: str,
    entry: float,
    bar_index: int,
    df: pd.DataFrame,
) -> TradePlan | None:
    start = bar_index - cfg.STOP_LOOKBACK
    if start < 0:
        return None

    row = df.iloc[bar_index]
    window = df.iloc[start:bar_index]
    prev_low = float(window["low"].min())
    prev_high = float(window["high"].max())
    ema30 = float(row["ema30"])
    ema55 = float(row["ema55"])

    result = calc_stop(entry, side, prev_low, prev_high, ema30, ema55)
    if result is None:
        return None

    stop, source = result
    r = abs(entry - stop)
    if r <= 0:
        return None

    risk = _risk_pct(entry, stop, side)

    if side == "long":
        tp_1r = entry + cfg.RR_PARTIAL_1 * r
        tp_2r = entry + cfg.RR_PARTIAL_2 * r
        tp_final = entry + cfg.RR_FINAL * r
    else:
        tp_1r = entry - cfg.RR_PARTIAL_1 * r
        tp_2r = entry - cfg.RR_PARTIAL_2 * r
        tp_final = entry - cfg.RR_FINAL * r

    return TradePlan(
        side=side,
        entry=entry,
        stop=stop,
        r=r,
        tp_1r=tp_1r,
        tp_2r=tp_2r,
        tp_final=tp_final,
        stop_source=source,
        risk_pct=risk,
    )


def build_hunting_trade_plan(side: str, entry: float, stop: float) -> TradePlan | None:
    """Hunting Funding：波段止損 + 1R/3R/5R 目標。"""
    risk = _risk_pct(entry, stop, side)
    if risk > cfg.HUNTING_MAX_SL_PCT / 100.0:
        return None
    r = abs(entry - stop)
    if r <= 0:
        return None
    if side == "long":
        tp_1r = entry + r
        tp_2r = entry + 3.0 * r
        tp_final = entry + 5.0 * r
    else:
        tp_1r = entry - r
        tp_2r = entry - 3.0 * r
        tp_final = entry - 5.0 * r
    margin = cfg.HUNTING_TOTAL_CAPITAL * cfg.HUNTING_POSITION_PCT / 100.0
    size = margin / entry if entry > 0 else 0.0
    return TradePlan(
        side=side,
        entry=entry,
        stop=stop,
        r=r,
        tp_1r=tp_1r,
        tp_2r=tp_2r,
        tp_final=tp_final,
        stop_source="hunting_swing",
        risk_pct=risk,
        position_size=size,
    )


def recalc_plan_for_fill(plan: TradePlan, fill_entry: float, strategy_id: str) -> TradePlan:
    """以實際成交價重算 R 與止盈（止損價不變）。"""
    stop = plan.stop
    side = plan.side
    r = abs(fill_entry - stop)
    if r <= 0:
        return TradePlan(
            side=side,
            entry=fill_entry,
            stop=stop,
            r=plan.r,
            tp_1r=plan.tp_1r,
            tp_2r=plan.tp_2r,
            tp_final=plan.tp_final,
            stop_source=plan.stop_source,
            risk_pct=plan.risk_pct,
            position_size=plan.position_size,
        )

    if strategy_id == "hunting_funding":
        rr1, rr2, rr_final = 1.0, 3.0, 5.0
    elif strategy_id == "donchian":
        rr1 = cfg.DONCHIAN_RR_TP1
        rr2 = cfg.DONCHIAN_RR_TP2
        rr_final = cfg.DONCHIAN_RR_TP3
    else:
        rr1 = cfg.RR_PARTIAL_1
        rr2 = cfg.RR_PARTIAL_2
        rr_final = cfg.RR_FINAL

    if side == "long":
        tp_1r = fill_entry + rr1 * r
        tp_2r = fill_entry + rr2 * r
        tp_final = fill_entry + rr_final * r
    else:
        tp_1r = fill_entry - rr1 * r
        tp_2r = fill_entry - rr2 * r
        tp_final = fill_entry - rr_final * r

    return TradePlan(
        side=side,
        entry=fill_entry,
        stop=stop,
        r=r,
        tp_1r=tp_1r,
        tp_2r=tp_2r,
        tp_final=tp_final,
        stop_source=plan.stop_source,
        risk_pct=_risk_pct(fill_entry, stop, side),
        position_size=plan.position_size,
    )


def calc_donchian_position_size(entry: float, stop: float) -> float:
    """定損 2U：倉位數量 = 風險金額 / 每單位價格風險。"""
    per_unit = abs(entry - stop)
    if per_unit <= 0:
        return 0.0
    return cfg.DONCHIAN_RISK_USDT / per_unit


def build_donchian_trade_plan(
    side: str,
    entry: float,
    donchian_upper: float,
    donchian_lower: float,
) -> TradePlan | None:
    """
    唐奇安止損：空單用上軌、多單用下軌，預留 1% 緩衝，總風險 ≤10%。
    止盈：1:2 / 1:5 / 1:10；倉位依定損 2U 計算。
    """
    buf = cfg.DONCHIAN_SL_BUFFER_PCT
    if side == "short":
        raw_sl = donchian_upper
        stop = raw_sl * (1 + buf)
    else:
        raw_sl = donchian_lower
        stop = raw_sl * (1 - buf)

    risk = _risk_pct(entry, stop, side)
    if risk > cfg.DONCHIAN_MAX_SL_PCT:
        return None

    r = abs(entry - stop)
    if r <= 0:
        return None

    size = calc_donchian_position_size(entry, stop)
    if size <= 0:
        return None

    rr1, rr2, rr3 = cfg.DONCHIAN_RR_TP1, cfg.DONCHIAN_RR_TP2, cfg.DONCHIAN_RR_TP3
    if side == "long":
        tp_1r = entry + rr1 * r
        tp_2r = entry + rr2 * r
        tp_final = entry + rr3 * r
    else:
        tp_1r = entry - rr1 * r
        tp_2r = entry - rr2 * r
        tp_final = entry - rr3 * r

    source = "donchian_upper" if side == "short" else "donchian_lower"
    return TradePlan(
        side=side,
        entry=entry,
        stop=stop,
        r=r,
        tp_1r=tp_1r,
        tp_2r=tp_2r,
        tp_final=tp_final,
        stop_source=source,
        risk_pct=risk,
        position_size=size,
    )
