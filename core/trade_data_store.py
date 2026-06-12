"""本地成交／訂單／自動交易 state 儲存（非 Binance 交易所紀錄）。"""

from __future__ import annotations

import json
import os
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ORDERS_DIR = _PROJECT_ROOT / "data" / "orders"
_STATE_DIR = _PROJECT_ROOT / "data" / "live_trader_state"
_LEGACY_ORDERS = _PROJECT_ROOT / "data" / "paper_orders.json"
_LEGACY_STATE = _PROJECT_ROOT / "data" / "live_trader_state.json"


def _truthy(val: str) -> bool:
    return val.strip().lower() in ("1", "true", "yes", "on")


def clear_local_trade_data() -> list[str]:
    """
    清空本機 bot 訂單與 live_runner 防重複 state。
    不會刪除 Binance 上的持倉／成交；不刪除 data/cache。
    """
    removed: list[str] = []
    for path in (_LEGACY_ORDERS, _LEGACY_STATE):
        if path.is_file():
            path.unlink()
            removed.append(str(path.relative_to(_PROJECT_ROOT)))

    for folder in (_ORDERS_DIR, _STATE_DIR):
        if not folder.is_dir():
            continue
        for f in folder.glob("*.json"):
            f.unlink()
            removed.append(str(f.relative_to(_PROJECT_ROOT)))

    _ORDERS_DIR.mkdir(parents=True, exist_ok=True)
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    return removed


def maybe_clear_from_env() -> list[str]:
    """CLEAR_TRADE_DATA=1 時啟動即清空（Zeabur 一次性設定用）。"""
    if _truthy(os.getenv("CLEAR_TRADE_DATA", "")):
        return clear_local_trade_data()
    return []
