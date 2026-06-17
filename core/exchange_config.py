"""目前使用的交易所（binance / okx）。"""

from __future__ import annotations

from core.env_bootstrap import env_value, load_project_env

load_project_env()


def active_exchange() -> str:
    raw = env_value("EXCHANGE", "binance").strip().lower()
    return raw or "binance"


def is_okx() -> bool:
    return active_exchange() == "okx"


def is_binance() -> bool:
    return active_exchange() == "binance"
