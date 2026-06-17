"""交易所市場資料（公開 API，不需密鑰）；依 EXCHANGE 分派 Binance / OKX。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Literal

import pandas as pd
import requests

from core.exchange_config import is_okx

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


MarketAPIError = BinanceAPIError


def strict_futures_only() -> bool:
    """設 BINANCE_STRICT_FUTURES=1 時，永續模式不 fallback 現貨（適合 Zeabur 亞洲機房）。"""
    return os.environ.get("BINANCE_STRICT_FUTURES", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def allow_spot_ws_fallback() -> bool:
    """
    永續模式是否允許瀏覽器 WebSocket 改連現貨 stream.binance.com。
    預設關閉（避免畫面標永續、實際卻是現貨價）；僅在明確設
    BINANCE_ALLOW_SPOT_WS_FALLBACK=1 時啟用（例如美國主機無法連 fstream）。
    """
    if strict_futures_only():
        return False
    return os.environ.get("BINANCE_ALLOW_SPOT_WS_FALLBACK", "").strip().lower() in (
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
    """抓取 OHLCV。market=futures 時優先永續合約 K 線。"""
    if is_okx() and market == "futures":
        from core.okx_market_data import OkxAPIError, fetch_klines as okx_klines

        try:
            return okx_klines(symbol, interval=interval, limit=limit)
        except OkxAPIError as exc:
            raise BinanceAPIError(str(exc)) from exc

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
    """24h ticker。回傳 (資料, 實際來源)；永續模式優先合約 ticker。"""
    if is_okx() and market == "futures":
        from core.okx_market_data import OkxAPIError, fetch_ticker_24h as okx_tickers

        try:
            rows, src = okx_tickers()
            return rows, src  # type: ignore[return-value]
        except OkxAPIError as exc:
            raise BinanceAPIError(str(exc)) from exc

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


def fetch_symbol_last_price(symbol: str, market: MarketType = "futures") -> float:
    """單一交易對最新成交價（REST）。"""
    if is_okx() and market == "futures":
        from core.okx_market_data import fetch_symbol_last_price as okx_last

        return okx_last(symbol)

    sym = symbol.replace("/", "").upper()
    params = {"symbol": sym}

    if market == "futures":
        candidates = [
            (f"{FUTURES_BASE}/fapi/v1/ticker/price", params),
        ]
        if not strict_futures_only():
            candidates.extend(
                [
                    (f"{SPOT_BASE}/api/v3/ticker/price", params),
                    (f"{SPOT_MIRROR_BASE}/api/v3/ticker/price", params),
                ]
            )
    else:
        candidates = [
            (f"{SPOT_BASE}/api/v3/ticker/price", params),
            (f"{SPOT_MIRROR_BASE}/api/v3/ticker/price", params),
        ]

    for url, p in candidates:
        try:
            resp = requests.get(url, params=p, timeout=10)
            if resp.status_code in (451, 403, 418):
                continue
            resp.raise_for_status()
            data = resp.json()
            price = float(data.get("price", 0) or 0)
            if price > 0:
                return price
        except (requests.RequestException, TypeError, ValueError):
            continue
    return 0.0


def symbol_display(binance_symbol: str) -> str:
    """BTCUSDT -> BTC/USDT"""
    s = binance_symbol.upper()
    if s.endswith("USDT"):
        return f"{s[:-4]}/USDT"
    return s


@dataclass
class OIFetchResult:
    """Binance 未平倉量歷史抓取結果（含錯誤訊息供 UI 顯示）。"""
    series: pd.Series
    ok: bool = True
    error: str = ""
    http_status: int | None = None
    fetched_count: int = 0
    symbol: str = ""
    period: str = ""
    source: str = "fapi:/futures/data/openInterestHist"


def _oi_error_message(exc: requests.RequestException, http_status: int | None) -> str:
    if http_status == 451:
        return (
            "HTTP 451：目前地區或主機無法連線 Binance 永續 API。"
            "請改用亞洲機房（如 Zeabur Singapore）或本機執行。"
        )
    if http_status == 403:
        return "HTTP 403：Binance API 拒絕連線，請稍後重試或檢查 IP 限制。"
    if http_status:
        return f"HTTP {http_status}：{exc}"
    return f"連線失敗：{exc}"


def fetch_open_interest_history(
    symbol: str,
    interval: str,
    limit: int = 500,
) -> OIFetchResult:
    """永續未平倉量歷史；與 K 線 datetime 對齊後使用。"""
    if is_okx():
        from core.okx_market_data import fetch_open_interest_history as okx_oi

        series, ok, error, count = okx_oi(symbol, interval, limit=limit)
        sym = symbol.replace("/", "").upper()
        period_map = {
            "1m": "5m", "3m": "5m", "5m": "5m", "15m": "15m",
            "30m": "30m", "1h": "1H", "2h": "2H", "4h": "4H",
            "6h": "6H", "12h": "12H", "1d": "1D",
        }
        period = period_map.get(interval, "5m")
        return OIFetchResult(
            series=series,
            ok=ok,
            error=error,
            fetched_count=count,
            symbol=sym,
            period=period,
            source="okx:/rubik/stat/contracts/open-interest-history",
        )

    period_map = {
        "1m": "5m", "3m": "5m", "5m": "5m", "15m": "15m",
        "30m": "30m", "1h": "1h", "2h": "2h", "4h": "4h",
        "6h": "6h", "12h": "12h", "1d": "1d",
    }
    period = period_map.get(interval, "5m")
    sym = symbol.replace("/", "").upper()
    url = f"{FUTURES_BASE}/futures/data/openInterestHist"
    params = {"symbol": sym, "period": period, "limit": min(limit, 500)}
    empty = pd.Series(dtype=float)
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        status = exc.response.status_code if exc.response is not None else None
        return OIFetchResult(
            series=empty,
            ok=False,
            error=_oi_error_message(exc, status),
            http_status=status,
            symbol=sym,
            period=period,
        )
    if not data:
        return OIFetchResult(
            series=empty,
            ok=False,
            error=f"API 回傳空資料（{sym} · period={period}）",
            http_status=200,
            symbol=sym,
            period=period,
        )
    series = pd.Series(
        {
            pd.Timestamp(d["timestamp"], unit="ms", tz="UTC"): float(d["sumOpenInterest"])
            for d in data
        }
    )
    return OIFetchResult(
        series=series,
        ok=True,
        fetched_count=len(series),
        symbol=sym,
        period=period,
    )
