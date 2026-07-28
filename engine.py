"""舊版 EMA/唐奇安逐根引擎已移除；回測請用各策略的 run_dashboard_backtest。"""

from __future__ import annotations


class TradingEngine:  # pragma: no cover
    def __init__(self, *args, **kwargs) -> None:
        raise RuntimeError(
            "TradingEngine（EMA/唐奇安）已移除；請使用 hunting_funding / hunting2 / smc_ict 回測路徑。"
        )
