"""技術指標計算。"""

from __future__ import annotations

import pandas as pd

import config as cfg


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    spans = {cfg.EMA_FAST, cfg.EMA_MID, cfg.EMA_SLOW, cfg.EMA_VOLUME_PRICE}
    for span in spans:
        out[f"ema{span}"] = out["close"].ewm(span=span, adjust=False).mean()

    out["vol_ma20"] = out["volume"].rolling(cfg.VOLUME_MA).mean()
    return out


def add_donchian_channels(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    n = cfg.DONCHIAN_LEN
    out["donchian_upper"] = out["high"].rolling(n).max()
    out["donchian_lower"] = out["low"].rolling(n).min()
    return out


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
    if cfg.STRATEGY == "donchian":
        return cfg.DONCHIAN_LEN + cfg.DONCHIAN_ENTRY_EXPIRE_BARS + 8
    warmup = max(cfg.EMA_SLOW, cfg.EMA_VOLUME_PRICE, cfg.VOLUME_MA) + 5
    return cfg.TREND_BARS_MIN + cfg.STOP_LOOKBACK + warmup
