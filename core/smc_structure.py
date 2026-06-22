"""SMC / ICT 市場結構：Swing、BOS/CHoCH、Order Block、Liquidity Sweep。"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

import config as cfg


@dataclass(frozen=True)
class SwingPoint:
    bar_index: int
    kind: str  # "high" | "low"
    price: float


@dataclass
class OrderBlock:
    side: str  # "long" | "short"
    bar_index: int
    low: float
    high: float
    bos_bar: int


@dataclass
class LiquiditySweep:
    side: str  # "long" | "short" — long = swept lows (bullish setup)
    bar_index: int
    level: float


@dataclass
class StructureState:
    bias: int = 0  # 1 bullish, -1 bearish
    swings: list[SwingPoint] = field(default_factory=list)
    last_swing_high: SwingPoint | None = None
    last_swing_low: SwingPoint | None = None
    active_bull_ob: OrderBlock | None = None
    active_bear_ob: OrderBlock | None = None
    recent_bull_bos: int | None = None
    recent_bear_bos: int | None = None
    recent_bull_sweep: LiquiditySweep | None = None
    recent_bear_sweep: LiquiditySweep | None = None


def find_confirmed_swings(df: pd.DataFrame, left: int, right: int) -> list[SwingPoint]:
    """在 bar i+right 時確認 bar i 的 swing。"""
    out: list[SwingPoint] = []
    n = len(df)
    for i in range(left, n - right):
        hi = float(df["high"].iloc[i])
        lo = float(df["low"].iloc[i])
        win_hi = df["high"].iloc[i - left : i + right + 1]
        win_lo = df["low"].iloc[i - left : i + right + 1]
        if hi >= float(win_hi.max()):
            out.append(SwingPoint(bar_index=i, kind="high", price=hi))
        if lo <= float(win_lo.min()):
            out.append(SwingPoint(bar_index=i, kind="low", price=lo))
    return out


def _find_order_block(df: pd.DataFrame, bos_bar: int, side: str, lookback: int) -> OrderBlock | None:
    start = max(0, bos_bar - lookback)
    if side == "long":
        for j in range(bos_bar - 1, start - 1, -1):
            row = df.iloc[j]
            if float(row["close"]) < float(row["open"]):
                return OrderBlock(
                    side="long",
                    bar_index=j,
                    low=float(row["low"]),
                    high=float(row["high"]),
                    bos_bar=bos_bar,
                )
    else:
        for j in range(bos_bar - 1, start - 1, -1):
            row = df.iloc[j]
            if float(row["close"]) > float(row["open"]):
                return OrderBlock(
                    side="short",
                    bar_index=j,
                    low=float(row["low"]),
                    high=float(row["high"]),
                    bos_bar=bos_bar,
                )
    return None


def _wick_beyond(level: float, wick: float, side: str) -> bool:
    tol = cfg.SMC_SWEEP_TOLERANCE_PCT / 100.0
    if side == "long":
        return wick < level * (1.0 - tol)
    return wick > level * (1.0 + tol)


def _close_reclaimed(row: pd.Series, level: float, side: str) -> bool:
    close = float(row["close"])
    if side == "long":
        return close > level
    return close < level


def detect_sweep(
    row: pd.Series,
    bar_index: int,
    swing: SwingPoint | None,
    side: str,
) -> LiquiditySweep | None:
    """掃蕩流動性：刺破 swing 後收盤收回。"""
    if swing is None:
        return None
    if side == "long" and swing.kind != "low":
        return None
    if side == "short" and swing.kind != "high":
        return None
    wick = float(row["low"]) if side == "long" else float(row["high"])
    if not _wick_beyond(swing.price, wick, side):
        return None
    if not _close_reclaimed(row, swing.price, side):
        return None
    return LiquiditySweep(side=side, bar_index=bar_index, level=swing.price)


def price_touches_ob(row: pd.Series, ob: OrderBlock) -> bool:
    lo = float(row["low"])
    hi = float(row["high"])
    close = float(row["close"])
    pad = cfg.SMC_OB_TOUCH_PAD_PCT / 100.0
    ob_lo = ob.low * (1.0 - pad)
    ob_hi = ob.high * (1.0 + pad)
    if not (hi >= ob_lo and lo <= ob_hi):
        return False
    if ob.side == "long":
        return close >= ob.low
    return close <= ob.high


def build_structure_states(df: pd.DataFrame) -> list[StructureState]:
    """逐根 K 線推進結構狀態（供策略引擎使用）。"""
    left = cfg.SMC_SWING_LEFT
    right = cfg.SMC_SWING_RIGHT
    swings = find_confirmed_swings(df, left, right)
    swing_by_confirm: dict[int, list[SwingPoint]] = {}
    for sp in swings:
        confirm_at = sp.bar_index + right
        swing_by_confirm.setdefault(confirm_at, []).append(sp)

    states: list[StructureState] = []
    st = StructureState()
    n = len(df)

    for i in range(n):
        for sp in swing_by_confirm.get(i, []):
            st.swings.append(sp)
            if sp.kind == "high":
                st.last_swing_high = sp
            else:
                st.last_swing_low = sp

        row = df.iloc[i]
        close = float(row["close"])

        if st.last_swing_high and close > st.last_swing_high.price:
            st.recent_bull_bos = i
            ob = _find_order_block(df, i, "long", cfg.SMC_OB_LOOKBACK)
            if ob:
                st.active_bull_ob = ob
            if st.bias <= 0:
                st.bias = 1
            else:
                st.bias = 1

        if st.last_swing_low and close < st.last_swing_low.price:
            st.recent_bear_bos = i
            ob = _find_order_block(df, i, "short", cfg.SMC_OB_LOOKBACK)
            if ob:
                st.active_bear_ob = ob
            if st.bias >= 0:
                st.bias = -1
            else:
                st.bias = -1

        bull_sw = detect_sweep(row, i, st.last_swing_low, "long")
        if bull_sw:
            st.recent_bull_sweep = bull_sw
        bear_sw = detect_sweep(row, i, st.last_swing_high, "short")
        if bear_sw:
            st.recent_bear_sweep = bear_sw

        states.append(
            StructureState(
                bias=st.bias,
                swings=list(st.swings),
                last_swing_high=st.last_swing_high,
                last_swing_low=st.last_swing_low,
                active_bull_ob=st.active_bull_ob,
                active_bear_ob=st.active_bear_ob,
                recent_bull_bos=st.recent_bull_bos,
                recent_bear_bos=st.recent_bear_bos,
                recent_bull_sweep=st.recent_bull_sweep,
                recent_bear_sweep=st.recent_bear_sweep,
            )
        )

    return states


def long_setup_valid(st: StructureState, bar_index: int) -> tuple[OrderBlock, float] | None:
    """BOS + sweep + OB 回踩皆滿足時回傳 (OB, sweep_level)。"""
    if st.bias != 1 or st.active_bull_ob is None:
        return None
    if st.recent_bull_bos is None or bar_index - st.recent_bull_bos > cfg.SMC_ENTRY_EXPIRE_BARS:
        return None
    if st.recent_bull_sweep is None:
        return None
    bos = st.recent_bull_bos
    sweep = st.recent_bull_sweep
    if sweep.bar_index > bos:
        return None
    if bos - sweep.bar_index > cfg.SMC_SWEEP_MAX_BARS:
        return None
    if bar_index - bos > cfg.SMC_ENTRY_EXPIRE_BARS:
        return None
    return st.active_bull_ob, sweep.level


def short_setup_valid(st: StructureState, bar_index: int) -> tuple[OrderBlock, float] | None:
    if st.bias != -1 or st.active_bear_ob is None:
        return None
    if st.recent_bear_bos is None or bar_index - st.recent_bear_bos > cfg.SMC_ENTRY_EXPIRE_BARS:
        return None
    if st.recent_bear_sweep is None:
        return None
    bos = st.recent_bear_bos
    sweep = st.recent_bear_sweep
    if sweep.bar_index > bos:
        return None
    if bos - sweep.bar_index > cfg.SMC_SWEEP_MAX_BARS:
        return None
    if bar_index - bos > cfg.SMC_ENTRY_EXPIRE_BARS:
        return None
    return st.active_bear_ob, sweep.level
