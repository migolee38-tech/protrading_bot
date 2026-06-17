"""OKX WebSocket 公開行情（K 線 / 成交 / 標記價）。"""

from __future__ import annotations

from core.market_data import MarketType
from core.okx_futures import to_inst_id

OKX_WS_PUBLIC = "wss://ws.okx.com:8443/ws/v5/public"
OKX_WS_BUSINESS = "wss://ws.okx.com:8443/ws/v5/business"

_CANDLE_CHANNEL = {
    "1m": "candle1m",
    "3m": "candle3m",
    "5m": "candle5m",
    "15m": "candle15m",
    "30m": "candle30m",
    "1h": "candle1H",
    "2h": "candle2H",
    "4h": "candle4H",
    "6h": "candle6H",
    "12h": "candle12H",
    "1d": "candle1D",
}


def inst_id_for_market(symbol: str, market: MarketType) -> str:
    sym = symbol.replace("/", "").upper()
    if market == "futures":
        return to_inst_id(sym)
    if sym.endswith("USDT"):
        return f"{sym[:-4]}-USDT"
    return sym


def candle_channel(interval: str) -> str:
    iv = (interval or "5m").strip().lower()
    return _CANDLE_CHANNEL.get(iv, f"candle{iv}")


def chart_ws_boot(symbol: str, interval: str, *, market: MarketType = "futures") -> dict:
    """供 lightweight_tv BOOT 使用的 OKX WS 設定。"""
    inst_id = inst_id_for_market(symbol, market)
    ch = candle_channel(interval)
    return {
        "exchange": "okx",
        "businessUrl": OKX_WS_BUSINESS,
        "publicUrl": OKX_WS_PUBLIC,
        "instId": inst_id,
        "candleChannel": ch,
        "tradesChannel": "trades",
        "markChannel": "mark-price" if market == "futures" else "",
    }
