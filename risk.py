"""止損、止盈與 R 計算。"""

from __future__ import annotations

from dataclasses import dataclass

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


def build_hunting2_trade_plan(side: str, entry: float, stop: float) -> TradePlan | None:
    """Hunting 2.0：波段止損（含 buffer）+ 1R/3R/5R 目標。"""
    risk = _risk_pct(entry, stop, side)
    if risk > cfg.HUNTING2_MAX_SL_PCT / 100.0:
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
    margin = cfg.HUNTING2_TOTAL_CAPITAL * cfg.HUNTING2_POSITION_PCT / 100.0
    size = margin / entry if entry > 0 else 0.0
    return TradePlan(
        side=side,
        entry=entry,
        stop=stop,
        r=r,
        tp_1r=tp_1r,
        tp_2r=tp_2r,
        tp_final=tp_final,
        stop_source="hunting2_swing",
        risk_pct=risk,
        position_size=size,
    )


def build_smc_trade_plan(
    side: str,
    entry: float,
    ob_low: float,
    ob_high: float,
    sweep_level: float,
) -> TradePlan | None:
    """SMC：止損在 OB 外側與 sweep 極值外側（取較遠者）。"""
    buf = cfg.SMC_SL_BUFFER_PCT
    if side == "long":
        raw_stop = min(ob_low, sweep_level)
        stop = raw_stop * (1.0 - buf)
    else:
        raw_stop = max(ob_high, sweep_level)
        stop = raw_stop * (1.0 + buf)

    risk = _risk_pct(entry, stop, side)
    if risk > cfg.SMC_MAX_SL_PCT / 100.0:
        return None
    r = abs(entry - stop)
    if r <= 0:
        return None

    rr2 = cfg.SMC_RR_TP2
    rr3 = cfg.SMC_RR_TP3
    if side == "long":
        tp_1r = entry + r
        tp_2r = entry + rr2 * r
        tp_final = entry + rr3 * r
    else:
        tp_1r = entry - r
        tp_2r = entry - rr2 * r
        tp_final = entry - rr3 * r

    margin = cfg.SMC_TOTAL_CAPITAL * cfg.SMC_POSITION_PCT / 100.0
    size = margin / entry if entry > 0 else 0.0
    return TradePlan(
        side=side,
        entry=entry,
        stop=stop,
        r=r,
        tp_1r=tp_1r,
        tp_2r=tp_2r,
        tp_final=tp_final,
        stop_source="smc_ob_sweep",
        risk_pct=risk,
        position_size=size,
    )


def build_ivb_trade_plan(
    side: str,
    entry: float,
    stop_ref: float,
    ib_range: float = 0.0,
    *,
    apply_buffer: bool = True,
) -> TradePlan | None:
    """
    IVB Order Flow：結構止損（VA / zone 外側）+ personal 風控幾何。
    tp_1r=1R 減倉；tp_2r / tp_final = protection（預設 2R）。
    """
    buf = cfg.IVB_SL_BUFFER_PCT if apply_buffer else 0.0
    if side == "long":
        stop = float(stop_ref) * (1.0 - buf)
        if stop >= entry:
            stop = entry * (1.0 - cfg.IVB_FALLBACK_SL_PCT / 100.0)
    else:
        stop = float(stop_ref) * (1.0 + buf)
        if stop <= entry:
            stop = entry * (1.0 + cfg.IVB_FALLBACK_SL_PCT / 100.0)

    risk = _risk_pct(entry, stop, side)
    if risk > cfg.IVB_MAX_SL_PCT / 100.0:
        return None
    r = abs(entry - stop)
    if r <= 0:
        return None

    # 可選：IB 高度投影作為 protection 上限參考（不強制縮短 TP）
    _ = ib_range

    rr1 = cfg.IVB_RR_TP1
    rr2 = cfg.IVB_RR_TP2
    rr_final = cfg.IVB_RR_FINAL
    if side == "long":
        tp_1r = entry + rr1 * r
        tp_2r = entry + rr2 * r
        tp_final = entry + rr_final * r
    else:
        tp_1r = entry - rr1 * r
        tp_2r = entry - rr2 * r
        tp_final = entry - rr_final * r

    risk_dollars = cfg.IVB_TOTAL_CAPITAL * cfg.IVB_RISK_PCT_PER_TRADE / 100.0
    size = risk_dollars / r if r > 0 else 0.0
    return TradePlan(
        side=side,
        entry=entry,
        stop=stop,
        r=r,
        tp_1r=tp_1r,
        tp_2r=tp_2r,
        tp_final=tp_final,
        stop_source="ivb_va_zone",
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

    if strategy_id == "smc_ict":
        rr1, rr2, rr_final = 1.0, cfg.SMC_RR_TP2, cfg.SMC_RR_TP3
    elif strategy_id == "ivb_orderflow":
        rr1, rr2, rr_final = cfg.IVB_RR_TP1, cfg.IVB_RR_TP2, cfg.IVB_RR_FINAL
    elif strategy_id in ("hunting_funding", "hunting2"):
        rr1, rr2, rr_final = 1.0, 3.0, 5.0
    else:
        raise ValueError(f"不支援的策略: {strategy_id}")

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
