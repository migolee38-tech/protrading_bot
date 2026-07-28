"""技術指標計算。"""

from __future__ import annotations

import pandas as pd

import config as cfg


def add_rsi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    out = df.copy()
    delta = out["close"].diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, float("nan"))
    out["rsi"] = 100 - (100 / (1 + rs))
    return out


def add_macd(
    df: pd.DataFrame,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pd.DataFrame:
    out = df.copy()
    ema_fast = out["close"].ewm(span=fast, adjust=False).mean()
    ema_slow = out["close"].ewm(span=slow, adjust=False).mean()
    out["macd"] = ema_fast - ema_slow
    out["macd_signal"] = out["macd"].ewm(span=signal, adjust=False).mean()
    out["macd_hist"] = out["macd"] - out["macd_signal"]
    return out


def min_bars_required() -> int:
    if cfg.STRATEGY == "hunting_funding":
        return (
            cfg.HUNTING_HTF_EMA_LEN
            + cfg.HUNTING_SL_SWING
            + cfg.HUNTING_COOLDOWN_BARS
            + cfg.HUNTING_LOOKBACK
            + 10
        )
    if cfg.STRATEGY == "hunting2":
        tf_min = max(cfg.timeframe_minutes(cfg.HUNTING2_TIMEFRAME), 1)
        h4_chart_bars = max(1, 240 // tf_min) * cfg.HUNTING2_H4_BARS
        return (
            max(cfg.HUNTING2_HTF_EMA_LEN, cfg.HUNTING2_TREND_EMA_LEN)
            + h4_chart_bars
            + cfg.HUNTING2_SL_SWING
            + cfg.HUNTING2_COOLDOWN_BARS
            + cfg.HUNTING2_LOOKBACK
            + 10
        )
    if cfg.STRATEGY == "smc_ict":
        return (
            cfg.SMC_SWING_LEFT
            + cfg.SMC_SWING_RIGHT
            + cfg.SMC_OB_LOOKBACK
            + cfg.SMC_ENTRY_EXPIRE_BARS
            + cfg.SMC_SWEEP_MAX_BARS
            + 20
        )
    return 200
