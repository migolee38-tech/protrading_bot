"""
EMA12/30/55 趨勢排列 + EMA12 交叉 EMA55 + 帶量突破 EMA20（只做順勢單邊）。

多單：EMA12 > EMA30 > EMA55 連續 >= 48 根；
     當根 EMA12 上穿 EMA55，且（當根或前一根）帶量且收盤 > EMA20。
空單：相反。
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

import config as cfg
from risk import TradePlan, build_trade_plan


@dataclass(frozen=True)
class Signal:
    bar_index: int
    side: str
    entry: float
    plan: TradePlan


def _bullish_alignment(row: pd.Series) -> bool:
    return row["ema12"] > row["ema30"] > row["ema55"]


def _bearish_alignment(row: pd.Series) -> bool:
    return row["ema12"] < row["ema30"] < row["ema55"]


def _trend_ok(df: pd.DataFrame, i: int, side: str) -> bool:
    start = i - cfg.TREND_BARS_MIN + 1
    if start < 0:
        return False

    for j in range(start, i + 1):
        row = df.iloc[j]
        if side == "long" and not _bullish_alignment(row):
            return False
        if side == "short" and not _bearish_alignment(row):
            return False
    return True


def _cross_side(df: pd.DataFrame, i: int) -> str | None:
    if i < 1:
        return None

    prev = df.iloc[i - 1]
    curr = df.iloc[i]
    p12, p55 = prev["ema12"], prev["ema55"]
    c12, c55 = curr["ema12"], curr["ema55"]

    if p12 <= p55 and c12 > c55:
        return "long"
    if p12 >= p55 and c12 < c55:
        return "short"
    return None


def _volume_breakout_bar(df: pd.DataFrame, bar: int, side: str) -> bool:
    """帶量（量 > 量 MA20）且價格突破/跌破 EMA20。"""
    row = df.iloc[bar]
    if pd.isna(row["vol_ma20"]) or row["vol_ma20"] <= 0:
        return False
    if row["volume"] <= row["vol_ma20"]:
        return False

    ema20 = row[f"ema{cfg.EMA_VOLUME_PRICE}"]
    if side == "long":
        return row["close"] > ema20
    return row["close"] < ema20


def _volume_ok_on_cross(df: pd.DataFrame, i: int, side: str) -> bool:
    """交叉當根或前一根符合帶量突破 EMA20。"""
    if _volume_breakout_bar(df, i, side):
        return True
    if i >= 1:
        return _volume_breakout_bar(df, i - 1, side)
    return False


def evaluate_bar(df: pd.DataFrame, i: int) -> Signal | None:
    side = _cross_side(df, i)
    if side is None:
        return None
    if cfg.ALLOWED_SIDE is not None and side != cfg.ALLOWED_SIDE:
        return None
    if not _trend_ok(df, i, side):
        return None
    if not _volume_ok_on_cross(df, i, side):
        return None

    row = df.iloc[i]
    entry = float(row["close"])
    plan = build_trade_plan(side, entry, i, df)
    if plan is None:
        return None

    return Signal(bar_index=i, side=side, entry=entry, plan=plan)


def scan_signals(df: pd.DataFrame) -> list[Signal]:
    signals: list[Signal] = []
    for i in range(len(df)):
        sig = evaluate_bar(df, i)
        if sig is not None:
            signals.append(sig)
    return signals
