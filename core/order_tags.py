"""Binance 訂單 clientOrderId：嵌入策略 ID，供成交回查。"""

from __future__ import annotations

import re

_PREFIX = "tb_"
_SEP = "__"
_MAX_LEN = 36
_VALID = re.compile(r"^[\.A-Z\:/a-z0-9_-]+$")


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


def parse_strategy_id(client_order_id: str | None) -> str | None:
    """從 clientOrderId 解析 strategy_id；無法解析則回傳 None。"""
    if not client_order_id:
        return None
    cid = str(client_order_id).strip()
    if not cid.startswith(_PREFIX):
        return None
    body = cid[len(_PREFIX) :]
    if _SEP not in body:
        return None
    strategy_id, _symbol = body.rsplit(_SEP, 1)
    strategy_id = strategy_id.strip()
    return strategy_id or None


def strategy_name_from_client_order_id(client_order_id: str | None) -> str:
    sid = parse_strategy_id(client_order_id)
    if not sid:
        return ""
    from core.strategy_registry import STRATEGIES

    return STRATEGIES[sid].name if sid in STRATEGIES else sid
