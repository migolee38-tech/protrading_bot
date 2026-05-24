"""幣安市場資料（公開 API，不需密鑰）。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

import pandas as pd
import requests

MarketType = Literal["futures", "spot"]

FUTURES_BASE = "https://fapi.binance.com"
SPOT_BASE = "https://api.binance.com"

INTERVAL_MAP = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "1h": "1h",
    "4h": "4h",
    "1d": "1d",
}


def _base(market: MarketType) -> str:
    return FUTURES_BASE if market == "futures" else SPOT_BASE


def fetch_klines(
    symbol: str,
    interval: str = "5m",
    limit: int = 500,
    market: MarketType = "futures",
) -> pd.DataFrame:
    """抓取 OHLCV。symbol 格式：BTCUSDT。"""
    sym = symbol.replace("/", "").upper()
    if market == "futures":
        url = f"{FUTURES_BASE}/fapi/v1/klines"
    else:
        url = f"{SPOT_BASE}/api/v3/klines"

    params = {"symbol": sym, "interval": interval, "limit": min(limit, 1500)}
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    rows = resp.json()

    df = pd.DataFrame(
        rows,
        columns=[
            "open_time",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "close_time",
            "quote_volume",
            "trades",
            "taker_buy_base",
            "taker_buy_quote",
            "ignore",
        ],
    )
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = df[col].astype(float)

    df["datetime"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    return df[["datetime", "open", "high", "low", "close", "volume"]].reset_index(drop=True)


def fetch_ticker_24h(market: MarketType = "futures") -> list[dict]:
    if market == "futures":
        url = f"{FUTURES_BASE}/fapi/v1/ticker/24hr"
    else:
        url = f"{SPOT_BASE}/api/v3/ticker/24hr"
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    return resp.json()


def symbol_display(binance_symbol: str) -> str:
    """BTCUSDT -> BTC/USDT"""
    s = binance_symbol.upper()
    if s.endswith("USDT"):
        return f"{s[:-4]}/USDT"
    return s
