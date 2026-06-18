"""每日 USDT 交易對成交量 Top N。"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd

from core.exchange_bridge import exchange_label, futures_universe_label
from core.exchange_config import active_exchange
from core.market_data import (
    BinanceAPIError,
    MarketType,
    PriceSource,
    fallback_symbols,
    fetch_ticker_24h,
    symbol_display,
)

_CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)

_PRICE_SOURCE_COL = "price_source"


def _cache_path(market: MarketType, top_n: int) -> Path:
    today = date.today().isoformat()
    exchange = active_exchange()
    return _CACHE_DIR / f"top_volume_{exchange}_{market}_{top_n}_{today}.json"


def _cache_is_valid_for_market(rows: list[dict], market: MarketType) -> bool:
    """永續模式不使用來源為現貨的舊快取（避免誤標為永續價）。"""
    if not rows:
        return False
    src = rows[0].get(_PRICE_SOURCE_COL, "futures")
    if market == "futures":
        return src == "futures"
    return True


def _static_universe(top_n: int, reason: str) -> pd.DataFrame:
    from core.market_data import _set_source_note

    syms = fallback_symbols(top_n)
    rows = [
        {
            "symbol": s,
            "pair": symbol_display(s),
            "quote_volume_24h": 0.0,
            "price_change_pct": 0.0,
            "last_price": 0.0,
            _PRICE_SOURCE_COL: "static",
        }
        for s in syms
    ]
    df = pd.DataFrame(rows).reset_index(drop=True)
    df["rank"] = df.index + 1
    _set_source_note(
        f"無法連線 {exchange_label()} API（{reason}），已顯示內建常用幣種清單；"
        "價格需待 API 恢復後更新。"
    )
    return df


def top_usdt_pairs_by_volume(
    top_n: int = 100,
    market: MarketType = "futures",
    use_cache: bool = True,
) -> pd.DataFrame:
    """依 24h 報價成交量排序；market=futures 時優先永續 fapi ticker。"""
    cache_file = _cache_path(market, top_n)
    if use_cache and cache_file.exists():
        with open(cache_file, encoding="utf-8") as f:
            rows = json.load(f)
        if _cache_is_valid_for_market(rows, market):
            return pd.DataFrame(rows)
        try:
            cache_file.unlink()
        except OSError:
            pass

    try:
        tickers, price_source = fetch_ticker_24h(market=market)
    except BinanceAPIError as exc:
        return _static_universe(top_n, str(exc))

    rows: list[dict] = []
    for t in tickers:
        sym = t.get("symbol", "")
        if not sym.endswith("USDT"):
            continue
        if market == "futures" and "_" in sym:
            continue
        qv = float(t.get("quoteVolume", 0) or 0)
        if qv <= 0:
            continue
        rows.append(
            {
                "symbol": sym,
                "pair": symbol_display(sym),
                "quote_volume_24h": qv,
                "price_change_pct": float(t.get("priceChangePercent", 0) or 0),
                "last_price": float(t.get("lastPrice", 0) or 0),
                _PRICE_SOURCE_COL: price_source,
            }
        )

    if not rows:
        return _static_universe(top_n, "ticker 回傳為空")

    df = (
        pd.DataFrame(rows)
        .sort_values("quote_volume_24h", ascending=False)
        .head(top_n)
        .reset_index(drop=True)
    )
    df["rank"] = df.index + 1

    # 僅在永續模式且確實來自 fapi 時，寫入永續快取
    if market != "futures" or price_source == "futures":
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(df.to_dict(orient="records"), f, ensure_ascii=False)
        except OSError:
            pass

    return df


def universe_price_source_label(df: pd.DataFrame) -> str:
    """供 UI 顯示榜單實際行情來源。"""
    if df.empty or _PRICE_SOURCE_COL not in df.columns:
        return "未知"
    src: PriceSource = df.iloc[0][_PRICE_SOURCE_COL]  # type: ignore[assignment]
    labels = {
        "futures": futures_universe_label(),
        "spot": "現貨",
        "spot_mirror": "現貨鏡像",
        "static": "內建清單",
    }
    return labels.get(src, str(src))
