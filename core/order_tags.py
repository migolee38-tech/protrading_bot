"""訂單 client order id：嵌入策略 ID，供成交回查（Binance / OKX）。"""

from __future__ import annotations

import re

_PREFIX = "tb_"
_SEP = "__"
_MAX_LEN = 36
_OKX_MAX_LEN = 32
_VALID = re.compile(r"^[\.A-Z\:/a-z0-9_-]+$")
_ALNUM = re.compile(r"[^a-zA-Z0-9]")


def build_client_order_id(strategy_id: str, symbol: str) -> str:
    """
    產生 Binance newClientOrderId（最長 36 字元）。
    格式：tb_{strategy_id}__{SYMBOL}
    """
    sid = (strategy_id or "unknown").strip()
    sym = symbol.replace("/", "").upper()
    cid = f"{_PREFIX}{sid}{_SEP}{sym}"
    if len(cid) <= _MAX_LEN and _VALID.match(cid):
        return cid

    # 極長交易對：縮短 symbol 以符合長度與字元規則
    budget = _MAX_LEN - len(_PREFIX) - len(_SEP) - len(sid)
    if budget < 4:
        sid = sid[: max(1, _MAX_LEN - len(_PREFIX) - len(_SEP) - 6)]
        budget = _MAX_LEN - len(_PREFIX) - len(_SEP) - len(sid)
    sym = sym[:budget]
    cid = f"{_PREFIX}{sid}{_SEP}{sym}"
    return cid[:_MAX_LEN]


def build_okx_client_order_id(strategy_id: str, symbol: str) -> str:
    """
    OKX clOrdId：僅英數字、最長 32、須以字母開頭。
    格式：tb{strategy_id}{SYMBOL}（移除非英數字）
    """
    sid = _ALNUM.sub("", (strategy_id or "unknown").strip())
    sym = _ALNUM.sub("", symbol.replace("/", "").upper())
    cid = f"tb{sid}{sym}"
    if not cid or not cid[0].isalpha():
        cid = f"t{cid}"
    if len(cid) <= _OKX_MAX_LEN:
        return cid
    # 優先保留策略 id，縮短 symbol
    budget = _OKX_MAX_LEN - 2 - len(sid)
    if budget < 4:
        sid = sid[: max(1, _OKX_MAX_LEN - 6)]
        budget = _OKX_MAX_LEN - 2 - len(sid)
    sym = sym[:budget]
    return f"tb{sid}{sym}"[:_OKX_MAX_LEN]


def parse_strategy_id(client_order_id: str | None) -> str | None:
    """從 clientOrderId 解析 strategy_id；無法解析則回傳 None。"""
    if not client_order_id:
        return None
    cid = str(client_order_id).strip()
    if cid.startswith(_PREFIX):
        body = cid[len(_PREFIX) :]
        if _SEP in body:
            strategy_id, _symbol = body.rsplit(_SEP, 1)
            strategy_id = strategy_id.strip()
            return strategy_id or None

    # OKX 緊湊格式：tb{strategy}{SYMBOL}
    if cid.startswith("tb") and len(cid) > 2:
        from core.strategy_registry import STRATEGIES

        body = cid[2:]
        for sid in sorted(STRATEGIES.keys(), key=len, reverse=True):
            if body.startswith(sid):
                return sid
    return None


def strategy_name_from_client_order_id(client_order_id: str | None) -> str:
    sid = parse_strategy_id(client_order_id)
    if not sid:
        return ""
    from core.strategy_registry import STRATEGIES

    return STRATEGIES[sid].name if sid in STRATEGIES else sid
