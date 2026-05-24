"""幣安市場資料（公開 API，不需密鑰）。"""

from __future__ import annotations

from typing import Any, Literal

import pandas as pd
import requests

MarketType = Literal["futures", "spot"]

# 主站（部分地區 / 雲端主機會回 451）
FUTURES_BASE = "https://fapi.binance.com"
SPOT_BASE = "https://api.binance.com"
# 官方公開行情鏡像（免 API Key，適合 Streamlit Cloud 等無法連主站的地區）
SPOT_MIRROR_BASE = "https://data-api.binance.vision"

INTERVAL_MAP = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "1h": "1h",
    "4h": "4h",
    "1d": "1d",
}

# 資料來源提示（可累積多則，供 UI 顯示）
_source_notes: list[str] = []

# API 全失敗時的靜態榜單（永續/現貨皆用 USDT 報價對）
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


def pop_source_note() -> str | None:
    """取出並清除所有 market_data 來源提示。"""
    global _source_notes
    if not _source_notes:
        return None
    joined = " ".join(_source_notes)
    _source_notes = []
    return joined


def _set_source_note(msg: str | None) -> None:
    global _source_notes
    if msg and msg not in _source_notes:
        _source_notes.append(msg)


def _get_json(candidates: list[tuple[str, dict[str, Any] | None]]) -> Any:
    """依序嘗試多個 URL，略過 451/403 等地區限制。"""
    last_exc: Exception | None = None
    for url, params in candidates:
        try:
            resp = requests.get(url, params=params or {}, timeout=60)
            if resp.status_code in (451, 403, 418):
                last_exc = requests.HTTPError(
                    f"{resp.status_code} for {url}", response=resp
                )
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            last_exc = exc
            continue
    raise BinanceAPIError(str(last_exc) if last_exc else "Binance API unavailable")


def fallback_symbols(top_n: int) -> list[str]:
    return list(_FALLBACK_SYMBOLS[: max(1, top_n)])


def fetch_klines(
    symbol: str,
    interval: str = "5m",
    limit: int = 500,
    market: MarketType = "futures",
) -> pd.DataFrame:
    """抓取 OHLCV。symbol 格式：BTCUSDT。"""
    sym = symbol.replace("/", "").upper()
    params = {"symbol": sym, "interval": interval, "limit": min(limit, 1500)}

    if market == "futures":
        candidates = [
            (f"{FUTURES_BASE}/fapi/v1/klines", params),
            (f"{SPOT_BASE}/api/v3/klines", params),
            (f"{SPOT_MIRROR_BASE}/api/v3/klines", params),
        ]
        note_on_mirror = (
            "永續 K 線主站不可用，暫以現貨 K 線（含官方鏡像）顯示，與合約價格可能略有差異。"
        )
    else:
        candidates = [
            (f"{SPOT_BASE}/api/v3/klines", params),
            (f"{SPOT_MIRROR_BASE}/api/v3/klines", params),
        ]
        note_on_mirror = "現貨 K 線使用官方鏡像 data-api.binance.vision。"

    used_mirror = False
    rows = None
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
            if i > 0:
                used_mirror = True
            break
        except requests.RequestException as exc:
            last_exc = exc
            continue

    if rows is None:
        raise BinanceAPIError(str(last_exc) if last_exc else "klines unavailable")

    if used_mirror:
        _set_source_note(note_on_mirror)

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
    """24h ticker；永續主站 451 時改抓現貨（含鏡像）。"""
    if market == "futures":
        candidates = [
            f"{FUTURES_BASE}/fapi/v1/ticker/24hr",
            f"{SPOT_BASE}/api/v3/ticker/24hr",
            f"{SPOT_MIRROR_BASE}/api/v3/ticker/24hr",
        ]
        mirror_note = (
            "Top 榜：永續行情主站不可用，已改用現貨 24h 成交量排序（含官方鏡像）。"
        )
    else:
        candidates = [
            f"{SPOT_BASE}/api/v3/ticker/24hr",
            f"{SPOT_MIRROR_BASE}/api/v3/ticker/24hr",
        ]
        mirror_note = "Top 榜使用官方鏡像 data-api.binance.vision。"

    last_exc: Exception | None = None
    for i, item in enumerate(candidates):
        url = item if isinstance(item, str) else item[0]
        try:
            resp = requests.get(url, timeout=60)
            if resp.status_code in (451, 403, 418):
                last_exc = requests.HTTPError(
                    f"{resp.status_code} for {url}", response=resp
                )
                continue
            resp.raise_for_status()
            if i > 0 and market == "futures":
                _set_source_note(mirror_note)
            elif i > 0 and market == "spot":
                _set_source_note(mirror_note)
            return resp.json()
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
