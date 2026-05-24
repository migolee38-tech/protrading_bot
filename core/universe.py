"""每日 USDT 交易對成交量 Top N。"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd

from core.market_data import MarketType, fetch_ticker_24h, symbol_display

_CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _cache_path(market: MarketType, top_n: int) -> Path:
    today = date.today().isoformat()
    return _CACHE_DIR / f"top_volume_{market}_{top_n}_{today}.json"


def top_usdt_pairs_by_volume(
    top_n: int = 100,
    market: MarketType = "futures",
    use_cache: bool = True,
) -> pd.DataFrame:
    """
    依 24h 報價成交量（quoteVolume）排序，回傳 USDT 永續/現貨交易對。
    """
    cache_file = _cache_path(market, top_n)
    if use_cache and cache_file.exists():
        with open(cache_file, encoding="utf-8") as f:
            rows = json.load(f)
        return pd.DataFrame(rows)

    tickers = fetch_ticker_24h(market=market)
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

    df = (
        pd.DataFrame(rows)
        .sort_values("quote_volume_24h", ascending=False)
        .head(top_n)
        .reset_index(drop=True)
    )
    df["rank"] = df.index + 1

    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(df.to_dict(orient="records"), f, ensure_ascii=False)

    return df
