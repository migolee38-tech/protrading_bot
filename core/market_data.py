"""幣安市場資料（公開 API，不需密鑰）。"""

from __future__ import annotations

import os
from typing import Any, Literal

import pandas as pd
import requests

MarketType = Literal["futures", "spot"]
PriceSource = Literal["futures", "spot", "spot_mirror", "static"]

# 主站（部分地區 / 雲端主機會回 451）
FUTURES_BASE = "https://fapi.binance.com"
SPOT_BASE = "https://api.binance.com"
# 官方公開行情鏡像（僅現貨 REST）
SPOT_MIRROR_BASE = "https://data-api.binance.vision"

INTERVAL_MAP = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "1h": "1h",
    "4h": "4h",
    "1d": "1d",
}

_source_notes: list[str] = []
_last_kline_source: PriceSource | None = None

_FALLBACK_SYMBOLS: tuple[str, ...] = (
    "BTCUSDT",
    "ETHUSDT",
    "BNBUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "DOGEUSDT",
    "ADAUSDT",
    "AVAXUSDT",
    "TRXUSDT",
    "LINKUSDT",
    "DOTUSDT",
    "MATICUSDT",
    "LTCUSDT",
    "BCHUSDT",
    "UNIUSDT",
    "ATOMUSDT",
    "ETCUSDT",
    "FILUSDT",
    "APTUSDT",
    "ARBUSDT",
    "OPUSDT",
    "NEARUSDT",
    "INJUSDT",
    "SUIUSDT",
    "PEPEUSDT",
    "WLDUSDT",
    "TIAUSDT",
    "SEIUSDT",
    "FETUSDT",
    "RENDERUSDT",
)


class BinanceAPIError(Exception):
    """所有候選端點皆無法取得資料。"""


def strict_futures_only() -> bool:
    """設 BINANCE_STRICT_FUTURES=1 時，永續模式不 fallback 現貨（適合 Zeabur 亞洲機房）。"""
    return os.environ.get("BINANCE_STRICT_FUTURES", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def pop_source_note() -> str | None:
    global _source_notes
    if not _source_notes:
        return None
    joined = " ".join(_source_notes)
    _source_notes = []
    return joined


def get_last_kline_source() -> PriceSource | None:
    return _last_kline_source


def _set_source_note(msg: str | None) -> None:
    global _source_notes
    if msg and msg not in _source_notes:
        _source_notes.append(msg)


def _source_from_url(url: str) -> PriceSource:
    if FUTURES_BASE in url:
        return "futures"
    if SPOT_MIRROR_BASE in url:
        return "spot_mirror"
    return "spot"


def fallback_symbols(top_n: int) -> list[str]:
    return list(_FALLBACK_SYMBOLS[: max(1, top_n)])


def fetch_klines(
    symbol: str,
    interval: str = "5m",
    limit: int = 500,
    market: MarketType = "futures",
) -> pd.DataFrame:
    """抓取 OHLCV。market=futures 時優先 fapi/v1/klines（永續）。"""
    global _last_kline_source

    sym = symbol.replace("/", "").upper()
    params = {"symbol": sym, "interval": interval, "limit": min(limit, 1500)}

    if market == "futures":
        candidates: list[tuple[str, dict[str, Any]]] = [
            (f"{FUTURES_BASE}/fapi/v1/klines", params),
        ]
        if not strict_futures_only():
            candidates.extend(
                [
                    (f"{SPOT_BASE}/api/v3/klines", params),
                    (f"{SPOT_MIRROR_BASE}/api/v3/klines", params),
                ]
            )
    else:
        candidates = [
            (f"{SPOT_BASE}/api/v3/klines", params),
            (f"{SPOT_MIRROR_BASE}/api/v3/klines", params),
        ]

    rows = None
    price_source: PriceSource = "futures" if market == "futures" else "spot"
    last_exc: Exception | None = None

    for i, (url, p) in enumerate(candidates):
        try:
            resp = requests.get(url, params=p, timeout=30)
            if resp.status_code in (451, 403, 418):
                last_exc = requests.HTTPError(
                    f"{resp.status_code} for {url}", response=resp
                )
                continue
            resp.raise_for_status()
            rows = resp.json()
            price_source = _source_from_url(url)
            if market == "futures" and price_source != "futures":
                _set_source_note(
                    "永續 K 線主站不可用，暫以現貨 K 線顯示，與合約價格可能略有差異。"
                )
            elif market == "spot" and price_source == "spot_mirror":
                _set_source_note("現貨 K 線使用官方鏡像 data-api.binance.vision。")
            break
        except requests.RequestException as exc:
            last_exc = exc
            continue

    if rows is None:
        _last_kline_source = None
        raise BinanceAPIError(str(last_exc) if last_exc else "klines unavailable")

    _last_kline_source = price_source

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
    out = df[["datetime", "open", "high", "low", "close", "volume"]].reset_index(
        drop=True
    )
    out.attrs["price_source"] = price_source
    return out


def fetch_ticker_24h(market: MarketType = "futures") -> tuple[list[dict], PriceSource]:
    """24h ticker。回傳 (資料, 實際來源)；永續模式優先 fapi/v1/ticker/24hr。"""
    if market == "futures":
        candidates = [f"{FUTURES_BASE}/fapi/v1/ticker/24hr"]
        if not strict_futures_only():
            candidates.extend(
                [
                    f"{SPOT_BASE}/api/v3/ticker/24hr",
                    f"{SPOT_MIRROR_BASE}/api/v3/ticker/24hr",
                ]
            )
    else:
        candidates = [
            f"{SPOT_BASE}/api/v3/ticker/24hr",
            f"{SPOT_MIRROR_BASE}/api/v3/ticker/24hr",
        ]

    last_exc: Exception | None = None
    for url in candidates:
        try:
            resp = requests.get(url, timeout=60)
            if resp.status_code in (451, 403, 418):
                last_exc = requests.HTTPError(
                    f"{resp.status_code} for {url}", response=resp
                )
                continue
            resp.raise_for_status()
            src = _source_from_url(url)
            if market == "futures" and src != "futures":
                _set_source_note(
                    "Top 榜：永續行情主站不可用，已改用現貨 24h 成交量排序。"
                )
            elif market == "spot" and src == "spot_mirror":
                _set_source_note("Top 榜使用官方鏡像 data-api.binance.vision。")
            return resp.json(), src
        except requests.RequestException as exc:
            last_exc = exc
            continue

    raise BinanceAPIError(str(last_exc) if last_exc else "ticker/24hr unavailable")


def symbol_display(binance_symbol: str) -> str:
    """BTCUSDT -> BTC/USDT"""
    s = binance_symbol.upper()
    if s.endswith("USDT"):
        return f"{s[:-4]}/USDT"
    return s
