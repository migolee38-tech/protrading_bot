"""回測報告：勝率、事件統計、匯出。"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from core.strategy_context import use_strategy
from core.strategy_registry import StrategyMeta, get_strategy
from indicators import min_bars_required


@dataclass
class BacktestResult:
    strategy_id: str
    symbol: str
    timeframe: str
    bars: int
    signal_count: int
    open_count: int
    win_count: int
    loss_count: int
    win_rate: float
    profit_factor: float = 0.0
    realized_pnl_usdt: float = 0.0
    unrealized_pnl_usdt: float = 0.0
    total_pnl_usdt: float = 0.0
    events: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        pf = self.profit_factor
        return {
            "strategy": self.strategy_id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "bars": self.bars,
            "signals": self.signal_count,
            "trades_opened": self.open_count,
            "wins": self.win_count,
            "losses": self.loss_count,
            "win_rate_pct": round(self.win_rate * 100, 2),
            "profit_factor": round(pf, 4) if pf != float("inf") else 9999.0,
            "realized_pnl_usdt": round(self.realized_pnl_usdt, 4),
            "unrealized_pnl_usdt": round(self.unrealized_pnl_usdt, 4),
            "total_pnl_usdt": round(self.total_pnl_usdt, 4),
        }


def _run_hunting_backtest(
    strategy_id: str,
    symbol: str,
    raw_df: pd.DataFrame,
    meta: StrategyMeta,
) -> BacktestResult:
    from core.strategy_registry import with_symbol
    from strategies.hunting_funding import run_dashboard_backtest

    df = meta.prepare_df(with_symbol(raw_df, symbol))
    with use_strategy(strategy_id):
        need = min_bars_required()
        if len(df) < need:
            return BacktestResult(
                strategy_id=strategy_id,
                symbol=symbol,
                timeframe=meta.timeframe,
                bars=len(df),
                signal_count=0,
                open_count=0,
                win_count=0,
                loss_count=0,
                win_rate=0.0,
                events=[f"K 線不足，需要至少 {need} 根"],
            )
        stats = run_dashboard_backtest(df)
    return BacktestResult(
        strategy_id=strategy_id,
        symbol=symbol,
        timeframe=meta.timeframe,
        bars=len(df),
        signal_count=stats["signal_count"],
        open_count=stats["open_count"],
        win_count=stats["wins"],
        loss_count=stats["losses"],
        win_rate=stats["win_rate"],
        profit_factor=stats.get("profit_factor", 0.0),
        realized_pnl_usdt=stats.get("realized_pnl_usdt", 0.0),
        unrealized_pnl_usdt=stats.get("unrealized_pnl_usdt", 0.0),
        total_pnl_usdt=stats.get("total_pnl_usdt", 0.0),
        events=stats["events"],
    )


def _run_hunting2_backtest(
    strategy_id: str,
    symbol: str,
    raw_df: pd.DataFrame,
    meta: StrategyMeta,
) -> BacktestResult:
    from core.strategy_registry import with_symbol
    from strategies.hunting2 import run_dashboard_backtest

    df = meta.prepare_df(with_symbol(raw_df, symbol))
    with use_strategy(strategy_id):
        need = min_bars_required()
        if len(df) < need:
            return BacktestResult(
                strategy_id=strategy_id,
                symbol=symbol,
                timeframe=meta.timeframe,
                bars=len(df),
                signal_count=0,
                open_count=0,
                win_count=0,
                loss_count=0,
                win_rate=0.0,
                events=[f"K 線不足，需要至少 {need} 根"],
            )
        stats = run_dashboard_backtest(df)
    return BacktestResult(
        strategy_id=strategy_id,
        symbol=symbol,
        timeframe=meta.timeframe,
        bars=len(df),
        signal_count=stats["signal_count"],
        open_count=stats["open_count"],
        win_count=stats["wins"],
        loss_count=stats["losses"],
        win_rate=stats["win_rate"],
        profit_factor=stats.get("profit_factor", 0.0),
        realized_pnl_usdt=stats.get("realized_pnl_usdt", 0.0),
        unrealized_pnl_usdt=stats.get("unrealized_pnl_usdt", 0.0),
        total_pnl_usdt=stats.get("total_pnl_usdt", 0.0),
        events=stats["events"],
    )


def _run_smc_backtest(
    strategy_id: str,
    symbol: str,
    raw_df: pd.DataFrame,
    meta: StrategyMeta,
) -> BacktestResult:
    from core.strategy_registry import with_symbol
    from strategies.smc_ict import run_dashboard_backtest

    df = meta.prepare_df(with_symbol(raw_df, symbol))
    with use_strategy(strategy_id):
        need = min_bars_required()
        if len(df) < need:
            return BacktestResult(
                strategy_id=strategy_id,
                symbol=symbol,
                timeframe=meta.timeframe,
                bars=len(df),
                signal_count=0,
                open_count=0,
                win_count=0,
                loss_count=0,
                win_rate=0.0,
                events=[f"K 線不足，需要至少 {need} 根"],
            )
        stats = run_dashboard_backtest(df)
    return BacktestResult(
        strategy_id=strategy_id,
        symbol=symbol,
        timeframe=meta.timeframe,
        bars=len(df),
        signal_count=stats["signal_count"],
        open_count=stats["open_count"],
        win_count=stats["wins"],
        loss_count=stats["losses"],
        win_rate=stats["win_rate"],
        profit_factor=stats.get("profit_factor", 0.0),
        realized_pnl_usdt=stats.get("realized_pnl_usdt", 0.0),
        unrealized_pnl_usdt=stats.get("unrealized_pnl_usdt", 0.0),
        total_pnl_usdt=stats.get("total_pnl_usdt", 0.0),
        events=stats["events"],
    )


def run_backtest(
    strategy_id: str,
    symbol: str,
    raw_df: pd.DataFrame,
    meta: StrategyMeta | None = None,
) -> BacktestResult:
    meta = meta or get_strategy(strategy_id)
    if strategy_id == "hunting_funding":
        return _run_hunting_backtest(strategy_id, symbol, raw_df, meta)
    if strategy_id == "hunting2":
        return _run_hunting2_backtest(strategy_id, symbol, raw_df, meta)
    if strategy_id == "smc_ict":
        return _run_smc_backtest(strategy_id, symbol, raw_df, meta)

    raise ValueError(f"不支援的策略回測: {strategy_id}")


def batch_backtest(
    strategy_ids: list[str],
    symbol: str,
    raw_df: pd.DataFrame,
) -> pd.DataFrame:
    rows = []
    for sid in strategy_ids:
        r = run_backtest(sid, symbol, raw_df)
        rows.append(r.to_dict())
    return pd.DataFrame(rows)
