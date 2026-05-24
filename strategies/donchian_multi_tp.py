"""
唐奇安通道 — 1H 實盤規則

訊號：Bear / Bull 型態（與 Pine 相同偏移）。
進場：訊號 K 的開盤價掛單，回踩觸價成交；24 根 1H 內未觸價則取消。
止損：唐奇安軌 ±1%，總風險 ≤10%；每筆定損 2U（倉位依 R 距離計算）。
出場：1:2 減倉 50% 止損移至開倉價（約鎖定 2U）；
      1:5 再減 50% 止損移至 3R，並以 peak-2R 移動止損；
      1:10 全平。
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

import config as cfg
from risk import TradePlan, build_donchian_trade_plan


@dataclass(frozen=True)
class Signal:
    bar_index: int
    signal_bar: int
    side: str
    entry: float
    plan: TradePlan


@dataclass
class PendingEntry:
    side: str
    limit_price: float
    signal_bar: int
    expires_at: int


def _bear_pattern(df: pd.DataFrame, i: int) -> bool:
    if i < 4:
        return False
    row_m3 = df.iloc[i - 3]
    row_m4 = df.iloc[i - 4]
    row_m2 = df.iloc[i - 2]
    row_m3c = df.iloc[i - 3]
    row_m1 = df.iloc[i - 1]
    row = df.iloc[i]
    return (
        row_m3["high"] > row_m4["donchian_upper"]
        and row_m2["close"] < row_m3c["close"]
        and row_m1["close"] < row_m3c["open"]
        and row["close"] < row_m1["open"]
    )


def _bull_pattern(df: pd.DataFrame, i: int) -> bool:
    if i < 4:
        return False
    row_m3 = df.iloc[i - 3]
    row_m4 = df.iloc[i - 4]
    row_m2 = df.iloc[i - 2]
    row_m3c = df.iloc[i - 3]
    row_m1 = df.iloc[i - 1]
    row = df.iloc[i]
    return (
        row_m3["low"] < row_m4["donchian_lower"]
        and row_m2["close"] > row_m3c["close"]
        and row_m1["close"] > row_m3c["open"]
        and row["close"] > row_m1["open"]
    )


def detect_signal_side(df: pd.DataFrame, i: int) -> str | None:
    if _bear_pattern(df, i):
        return "short"
    if _bull_pattern(df, i):
        return "long"
    return None


def _touch_limit(row: pd.Series, limit: float) -> bool:
    lo, hi = float(row["low"]), float(row["high"])
    return lo <= limit <= hi


def create_pending(df: pd.DataFrame, i: int) -> PendingEntry | None:
    side = detect_signal_side(df, i)
    if side is None:
        return None
    if cfg.ALLOWED_SIDE is not None and side != cfg.ALLOWED_SIDE:
        return None

    limit = float(df.iloc[i]["open"])
    expire = i + cfg.DONCHIAN_ENTRY_EXPIRE_BARS
    return PendingEntry(side=side, limit_price=limit, signal_bar=i, expires_at=expire)


def try_fill_pending(df: pd.DataFrame, i: int, pending: PendingEntry) -> Signal | None:
    if i <= pending.signal_bar:
        return None
    if i > pending.expires_at:
        return None

    row = df.iloc[i]
    if not _touch_limit(row, pending.limit_price):
        return None

    upper = float(row["donchian_upper"])
    lower = float(row["donchian_lower"])
    if pd.isna(upper) or pd.isna(lower):
        return None

    plan = build_donchian_trade_plan(
        pending.side, pending.limit_price, upper, lower
    )
    if plan is None:
        return None

    return Signal(
        bar_index=i,
        signal_bar=pending.signal_bar,
        side=pending.side,
        entry=pending.limit_price,
        plan=plan,
    )


def is_pending_expired(i: int, pending: PendingEntry) -> bool:
    return i > pending.expires_at


def scan_signals(df: pd.DataFrame) -> list[Signal]:
    signals: list[Signal] = []
    pending: PendingEntry | None = None

    for i in range(len(df)):
        if pending is not None:
            if is_pending_expired(i, pending):
                pending = None
            else:
                sig = try_fill_pending(df, i, pending)
                if sig is not None:
                    signals.append(sig)
                    pending = None

        if pending is None:
            new_pending = create_pending(df, i)
            if new_pending is not None:
                pending = new_pending

    return signals
