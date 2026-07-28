"""
交易機器人入口說明。

EMA 趨勢交叉與唐奇安已移除；請使用 Streamlit 儀表板或 live_runner。

用法：
  streamlit run streamlit_app.py
  RUNNER_STRATEGIES=hunting2 python live_runner.py --profiles all
"""

from __future__ import annotations

from core.strategy_registry import list_strategies


def main() -> None:
    print("EMA / 唐奇安策略已移除。")
    print("可用策略：")
    for s in list_strategies():
        print(f"  - {s.id}: {s.name} ({s.timeframe})")
    print("\n請執行：streamlit run streamlit_app.py")
    print("或：RUNNER_STRATEGIES=hunting2 python live_runner.py --profiles all")


if __name__ == "__main__":
    main()
