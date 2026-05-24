"""策略註冊表：4 套策略的元資料與資料準備。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import pandas as pd

import config as cfg
from indicators import add_donchian_channels, add_indicators, add_macd, add_rsi, min_bars_required


@dataclass(frozen=True)
class StrategyMeta:
    id: str
    name: str
    description: str
    timeframe: str

    def prepare_df(self, raw: pd.DataFrame) -> pd.DataFrame:
        if self.id == "donchian":
            df = add_indicators(raw)
            return add_donchian_channels(df)
        if self.id in ("ema", "rsi", "macd"):
            df = add_indicators(raw)
            if self.id == "rsi":
                return add_rsi(df)
            if self.id == "macd":
                return add_macd(df)
            return df
        return add_indicators(raw)

    def min_bars(self) -> int:
        with _patch_strategy(self.id):
            return min_bars_required()


def _patch_strategy(strategy_id: str):
    from core.strategy_context import use_strategy

    return use_strategy(strategy_id)


STRATEGIES: dict[str, StrategyMeta] = {
    "ema": StrategyMeta(
        id="ema",
        name="EMA 趨勢交叉",
        description="EMA12/30/55 排列 + 金叉/死叉 + 帶量突破 EMA20",
        timeframe=cfg.TIMEFRAME,
    ),
    "donchian": StrategyMeta(
        id="donchian",
        name="唐奇安多階止盈",
        description="唐奇安通道突破回踩 + 多階段止盈",
        timeframe=cfg.DONCHIAN_TIMEFRAME,
    ),
    "rsi": StrategyMeta(
        id="rsi",
        name="RSI 超買超賣反轉",
        description="RSI 進入超賣/超買區後回到中性區順勢進場",
        timeframe=cfg.TIMEFRAME,
    ),
    "macd": StrategyMeta(
        id="macd",
        name="MACD 動能交叉",
        description="MACD 線與訊號線金叉/死叉 + 柱狀體同向",
        timeframe=cfg.TIMEFRAME,
    ),
}


def list_strategies() -> list[StrategyMeta]:
    return list(STRATEGIES.values())


def get_strategy(strategy_id: str) -> StrategyMeta:
    if strategy_id not in STRATEGIES:
        raise KeyError(f"未知策略: {strategy_id}")
    return STRATEGIES[strategy_id]


def scan_signals_for(strategy_id: str, df: pd.DataFrame) -> list:
    from core.strategy_context import use_strategy

    with use_strategy(strategy_id):
        if strategy_id == "donchian":
            from strategies.donchian_multi_tp import scan_signals

            return scan_signals(df)
        if strategy_id == "ema":
            from strategies.ema_trend_cross import scan_signals

            return scan_signals(df)
        if strategy_id == "rsi":
            from strategies.rsi_reversal import scan_signals

            return scan_signals(df)
        if strategy_id == "macd":
            from strategies.macd_momentum import scan_signals

            return scan_signals(df)
    return []
