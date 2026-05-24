"""
交易機器人入口（目前不接 API，用示範資料驗證策略）。

用法：
  cd trading-bot
  source .venv/bin/activate
  python main.py
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import config as cfg
from engine import TradingEngine
from indicators import add_donchian_channels, add_indicators, min_bars_required

if cfg.STRATEGY == "donchian":
    from strategies.donchian_multi_tp import scan_signals
else:
    from strategies.ema_trend_cross import scan_signals


def _demo_ohlcv(n: int = 800) -> pd.DataFrame:
    """產生可跑策略的示範 K 線（僅供本地測試）。"""
    rng = np.random.default_rng(42)
    close = 100 + np.cumsum(rng.normal(0.05, 0.8, n))
    high = close + rng.uniform(0.2, 1.5, n)
    low = close - rng.uniform(0.2, 1.5, n)
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    volume = rng.uniform(50_000, 200_000, n)

    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )


def _print_plan(sig) -> None:
    p = sig.plan
    fill_bar = sig.bar_index
    sig_bar = getattr(sig, "signal_bar", fill_bar)
    print(f"\n--- 成交 @ K#{fill_bar}（訊號 K#{sig_bar}）---")
    print(f"方向: {sig.side}")
    print(f"進場: {p.entry:.4f}")
    print(f"止損: {p.stop:.4f} ({p.stop_source}, 風險約 {p.risk_pct * 100:.2f}%)")
    if cfg.STRATEGY == "donchian":
        print(f"倉位: {p.position_size:.6f}（定損 {cfg.DONCHIAN_RISK_USDT}U）")
        print(f"TP1 (1:2, 減50%≈鎖2U): {p.tp_1r:.4f}")
        print(f"TP2 (1:5, 再減50%→止損3R): {p.tp_2r:.4f}")
        print(f"TP3 (1:10 全平): {p.tp_final:.4f}")
    else:
        print(f"1:1 減{cfg.REDUCE_AT_1R_PCT:.0%} + 套保開倉價: {p.tp_1r:.4f}")
        print(f"1:2 減{cfg.REDUCE_AT_2R_PCT:.0%} + 套保1R + 移動停利: {p.tp_2r:.4f}")
        print(f"1:10 終極止盈: {p.tp_final:.4f}")


def main() -> None:
    raw = _demo_ohlcv()
    df = add_indicators(raw)
    if cfg.STRATEGY == "donchian":
        df = add_donchian_channels(df)

    need = min_bars_required()
    if len(df) < need:
        print(f"K 線不足，至少需要 {need} 根，目前 {len(df)} 根")
        return

    side_note = cfg.ALLOWED_SIDE or "順勢多空"
    strat_name = "唐奇安多階段止盈" if cfg.STRATEGY == "donchian" else "EMA趨勢交叉"
    tf = cfg.active_timeframe()
    print(f"策略: {strat_name} | 週期: {tf} | 方向: {side_note}")
    if cfg.STRATEGY == "donchian":
        print(
            f"進場: 訊號K開盤價回踩 | {cfg.DONCHIAN_ENTRY_EXPIRE_BARS}h 未觸價取消 | "
            f"定損 {cfg.DONCHIAN_RISK_USDT}U"
        )
    print(f"規則: 同幣單倉 | {cfg.MAX_CONSECUTIVE_SL}次連續止損→冷卻{cfg.COOLDOWN_HOURS}h")

    signals = scan_signals(df)
    print(f"掃描 K 線: {len(df)} 根，理論訊號: {len(signals)} 筆")

    engine = TradingEngine(
        symbol=cfg.DEFAULT_SYMBOL,
        bar_minutes=cfg.timeframe_minutes(),
    )
    engine.cooldown.load()
    log = engine.run(df)
    engine.cooldown.save()

    print(f"引擎事件: {len(log.entries)} 條")
    for line in log.entries[-15:]:
        print(line)

    if signals:
        print("\n最近訊號範例:")
        for sig in signals[-2:]:
            _print_plan(sig)
    elif not log.entries:
        print("此示範資料無訊號/成交；接上真實 5 分 K 後再測。")


if __name__ == "__main__":
    main()
