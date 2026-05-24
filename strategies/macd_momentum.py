"""MACD 金叉/死叉 + 柱狀體同向確認。"""

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


def _cross_side(df: pd.DataFrame, i: int) -> str | None:
    if i < 1:
        return None
    prev, curr = df.iloc[i - 1], df.iloc[i]
    if pd.isna(curr["macd"]) or pd.isna(curr["macd_signal"]):
        return None

    if prev["macd"] <= prev["macd_signal"] and curr["macd"] > curr["macd_signal"]:
        if curr["macd_hist"] > 0:
            return "long"
    if prev["macd"] >= prev["macd_signal"] and curr["macd"] < curr["macd_signal"]:
        if curr["macd_hist"] < 0:
            return "short"
    return None


def evaluate_bar(df: pd.DataFrame, i: int) -> Signal | None:
    side = _cross_side(df, i)
    if side is None:
        return None
    if cfg.ALLOWED_SIDE is not None and side != cfg.ALLOWED_SIDE:
        return None

    row = df.iloc[i]
    entry = float(row["close"])
    plan = build_trade_plan(side, entry, i, df)
    if plan is None:
        return None
    return Signal(bar_index=i, side=side, entry=entry, plan=plan)


def scan_signals(df: pd.DataFrame) -> list[Signal]:
    out: list[Signal] = []
    for i in range(len(df)):
        sig = evaluate_bar(df, i)
        if sig is not None:
            out.append(sig)
    return out
