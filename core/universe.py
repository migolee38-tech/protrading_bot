"""每日 USDT 交易對成交量 Top N。"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd

from core.market_data import (
    BinanceAPIError,
    MarketType,
    fallback_symbols,
    fetch_ticker_24h,
    symbol_display,
)

_CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _cache_path(market: MarketType, top_n: int) -> Path:
    today = date.today().isoformat()
    return _CACHE_DIR / f"top_volume_{market}_{top_n}_{today}.json"


def _static_universe(top_n: int, reason: str) -> pd.DataFrame:
    """API 全失敗時的內建榜單，避免 Streamlit Cloud 整頁 traceback。"""
    from core.market_data import _set_source_note

    syms = fallback_symbols(top_n)
    rows = [
        {
            "symbol": s,
            "pair": symbol_display(s),
            "quote_volume_24h": 0.0,
            "price_change_pct": 0.0,
            "last_price": 0.0,
        }
        for s in syms
    ]
    df = pd.DataFrame(rows).reset_index(drop=True)
    df["rank"] = df.index + 1
    _set_source_note(
        f"無法連線幣安 API（{reason}），已顯示內建常用幣種清單；"
        "即時 K 線若仍失敗，請改選現貨或稍後再試。"
    )
    return df


def top_usdt_pairs_by_volume(
    top_n: int = 100,
    market: MarketType = "futures",
    use_cache: bool = True,
) -> pd.DataFrame:
    """依 24h 報價成交量（quoteVolume）排序，回傳 USDT 永續/現貨交易對。"""
    cache_file = _cache_path(market, top_n)
    if use_cache and cache_file.exists():
        with open(cache_file, encoding="utf-8") as f:
            rows = json.load(f)
        return pd.DataFrame(rows)

    try:
        tickers = fetch_ticker_24h(market=market)
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

    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(df.to_dict(orient="records"), f, ensure_ascii=False)
    except OSError:
        pass

    return df
