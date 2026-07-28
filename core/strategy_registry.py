"""策略註冊表：儀表板策略元資料與資料準備。"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

import config as cfg
from indicators import min_bars_required


@dataclass(frozen=True)
class StrategyMeta:
    id: str
    name: str
    description: str
    timeframe: str

    def prepare_df(self, raw: pd.DataFrame) -> pd.DataFrame:
        if self.id == "hunting_funding":
            from strategies.hunting_funding import prepare_dataframe

            return prepare_dataframe(raw)
        if self.id == "hunting2":
            from strategies.hunting2 import prepare_dataframe

            return prepare_dataframe(raw)
        if self.id == "smc_ict":
            from strategies.smc_ict import prepare_dataframe

            return prepare_dataframe(raw)
        return raw.copy()

    def min_bars(self) -> int:
        with _patch_strategy(self.id):
            return min_bars_required()


def _patch_strategy(strategy_id: str):
    from core.strategy_context import use_strategy

    return use_strategy(strategy_id)


def with_symbol(
    raw: pd.DataFrame,
    symbol: str,
    kline_limit: int | None = None,
) -> pd.DataFrame:
    """附加 symbol / kline_limit 至 attrs，供 Hunting 抓取 OI。"""
    out = raw.copy()
    out.attrs["symbol"] = symbol.replace("/", "").upper()
    if kline_limit is not None:
        out.attrs["kline_limit"] = int(kline_limit)
    if hasattr(raw, "attrs") and "price_source" in raw.attrs:
        out.attrs["price_source"] = raw.attrs["price_source"]
    return out


STRATEGIES: dict[str, StrategyMeta] = {
    "hunting_funding": StrategyMeta(
        id="hunting_funding",
        name="Hunting Funding",
        description="OI/CVD/量能/EMA150 趨勢/動能五星評分 · 1R減倉30% · 5R全出",
        timeframe=cfg.HUNTING_FUNDING_TIMEFRAME,
    ),
    "hunting2": StrategyMeta(
        id="hunting2",
        name="Hunting 2.0",
        description="五星合流 + EMA150 距離閘門 + 4H 收盤突破 + SL buffer · 1R減倉30% · 5R全出",
        timeframe=cfg.HUNTING2_TIMEFRAME,
    ),
    "smc_ict": StrategyMeta(
        id="smc_ict",
        name="SMC / ICT",
        description="15m BOS + Liquidity Sweep + Order Block 回踩 · 1R減倉30% · 5R全出",
        timeframe=cfg.SMC_TIMEFRAME,
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
        if strategy_id == "hunting_funding":
            from strategies.hunting_funding import scan_signals

            return scan_signals(df)
        if strategy_id == "hunting2":
            from strategies.hunting2 import scan_signals

            return scan_signals(df)
        if strategy_id == "smc_ict":
            from strategies.smc_ict import scan_signals

            return scan_signals(df)
    return []
