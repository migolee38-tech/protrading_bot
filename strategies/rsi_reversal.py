"""RSI 超買超賣反轉：超賣後回升做多、超買後回落做空。"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

import config as cfg
from risk import TradePlan, build_trade_plan

RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70
RSI_EXIT_LOW = 35
RSI_EXIT_HIGH = 65


@dataclass(frozen=True)
class Signal:
    bar_index: int
    side: str
    entry: float
    plan: TradePlan


def evaluate_bar(df: pd.DataFrame, i: int) -> Signal | None:
    if i < 2:
        return None
    prev = df.iloc[i - 1]
    curr = df.iloc[i]
    if pd.isna(curr.get("rsi")) or pd.isna(prev.get("rsi")):
        return None

    side: str | None = None
    if prev["rsi"] < RSI_OVERSOLD and curr["rsi"] >= RSI_EXIT_LOW:
        side = "long"
    elif prev["rsi"] > RSI_OVERBOUGHT and curr["rsi"] <= RSI_EXIT_HIGH:
        side = "short"

    if side is None:
        return None
    if cfg.ALLOWED_SIDE is not None and side != cfg.ALLOWED_SIDE:
        return None

    entry = float(curr["close"])
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
