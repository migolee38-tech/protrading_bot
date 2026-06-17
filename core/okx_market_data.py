"""OKX 公開行情（REST，不需 API 金鑰）。"""

from __future__ import annotations

from typing import Any

import pandas as pd
import requests

from core.okx_futures import from_inst_id, to_inst_id

OKX_BASE = "https://www.okx.com"
_TIMEOUT = 30

_BAR_MAP = {
    "1m": "1m",
    "3m": "3m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1H",
    "2h": "2H",
    "4h": "4H",
    "6h": "6H",
    "12h": "12H",
    "1d": "1D",
}


class OkxAPIError(Exception):
    """OKX 公開 API 請求失敗。"""


def _get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    url = f"{OKX_BASE}{path}"
    try:
        resp = requests.get(url, params=params or {}, timeout=_TIMEOUT)
        resp.raise_for_status()
        payload = resp.json()
    except requests.RequestException as exc:
        raise OkxAPIError(str(exc)) from exc
    code = str(payload.get("code", ""))
    if code != "0":
        raise OkxAPIError(str(payload.get("msg") or f"OKX API {code}"))
    return payload


def fetch_klines(
    symbol: str,
    interval: str = "5m",
    limit: int = 500,
) -> pd.DataFrame:
    """SWAP K 線；回傳欄位與 Binance market_data 一致。"""
    inst_id = to_inst_id(symbol)
    bar = _BAR_MAP.get(interval, interval)
    payload = _get(
        "/api/v5/market/candles",
        {"instId": inst_id, "bar": bar, "limit": str(min(limit, 300))},
    )
    rows = payload.get("data") or []
    if not rows:
        raise OkxAPIError(f"{inst_id} K 線為空")

    # OKX 回傳最新在前，轉成時間正序
    rows = list(reversed(rows))
    records: list[dict[str, Any]] = []
    for row in rows:
        if len(row) < 6:
            continue
        records.append(
            {
                "open_time": int(row[0]),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
            }
        )
    df = pd.DataFrame(records)
    df["datetime"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    out = df[["datetime", "open", "high", "low", "close", "volume"]].reset_index(drop=True)
    out.attrs["price_source"] = "futures"
    return out


def fetch_ticker_24h() -> tuple[list[dict], str]:
    """SWAP 24h ticker；欄位對齊 Binance universe 解析。"""
    payload = _get("/api/v5/market/tickers", {"instType": "SWAP"})
    rows = payload.get("data") or []
    out: list[dict] = []
    for row in rows:
        inst_id = str(row.get("instId", ""))
        if not inst_id.endswith("-USDT-SWAP"):
            continue
        sym = from_inst_id(inst_id)
        last = float(row.get("last") or 0)
        open24 = float(row.get("open24h") or 0)
        if last <= 0:
            continue
        pct = ((last - open24) / open24 * 100.0) if open24 > 0 else 0.0
        qv = float(row.get("volCcy24h") or row.get("vol24h") or 0)
        out.append(
            {
                "symbol": sym,
                "quoteVolume": qv,
                "priceChangePercent": pct,
                "lastPrice": last,
            }
        )
    return out, "futures"


def fetch_symbol_last_price(symbol: str) -> float:
    inst_id = to_inst_id(symbol)
    payload = _get("/api/v5/market/ticker", {"instId": inst_id})
    rows = payload.get("data") or []
    if not rows:
        return 0.0
    return float(rows[0].get("last") or 0)


def fetch_open_interest_history(
    symbol: str,
    interval: str,
    limit: int = 500,
) -> tuple[pd.Series, bool, str, int]:
    """未平倉量歷史；回傳 (series, ok, error, count)。"""
    period_map = {
        "1m": "5m",
        "3m": "5m",
        "5m": "5m",
        "15m": "15m",
        "30m": "30m",
        "1h": "1H",
        "2h": "2H",
        "4h": "4H",
        "6h": "6H",
        "12h": "12H",
        "1d": "1D",
    }
    period = period_map.get(interval, "5m")
    inst_id = to_inst_id(symbol)
    empty = pd.Series(dtype=float)
    try:
        payload = _get(
            "/api/v5/rubik/stat/contracts/open-interest-history",
            {
                "instId": inst_id,
                "period": period,
                "limit": str(min(limit, 100)),
            },
        )
    except OkxAPIError as exc:
        return empty, False, str(exc), 0

    rows = payload.get("data") or []
    if not rows:
        return empty, False, f"API 回傳空資料（{inst_id} · period={period}）", 0

    # [ts, oi, oiCcy, oiUsd]
    series = pd.Series(
        {
            pd.Timestamp(int(row[0]), unit="ms", tz="UTC"): float(row[1])
            for row in rows
            if row and len(row) >= 2
        }
    )
    return series, True, "", len(series)
