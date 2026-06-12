"""依下單模式（paper / testnet / live）與帳戶 ID 載入 Binance API 金鑰。"""

from __future__ import annotations

import os
from enum import Enum
from pathlib import Path

from dotenv import load_dotenv

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_ENV_PATH, override=True)


class ExecMode(str, Enum):
    PAPER = "paper"
    TESTNET = "testnet"
    LIVE = "live"


def mode_label(mode: ExecMode | str) -> str:
    labels = {
        ExecMode.PAPER: "本地模擬",
        ExecMode.TESTNET: "Testnet 模擬倉",
        ExecMode.LIVE: "主網實盤",
    }
    if isinstance(mode, str):
        try:
            mode = ExecMode(mode)
        except ValueError:
            return mode
    return labels.get(mode, str(mode))


def load_credentials(
    mode: ExecMode | str,
    account_id: str = "account1",
) -> tuple[str, str]:
    """依模式與帳戶回傳 (api_key, api_secret)。"""
    from core.account_profiles import credentials_for_profile, load_profile

    if isinstance(mode, str):
        mode = ExecMode(mode)
    return credentials_for_profile(load_profile(account_id, mode))


def credentials_configured(
    mode: ExecMode | str,
    account_id: str = "account1",
) -> bool:
    from core.account_profiles import load_profile, profile_configured

    if isinstance(mode, str):
        mode = ExecMode(mode)
    return profile_configured(load_profile(account_id, mode))


def credentials_hint(
    mode: ExecMode | str,
    account_id: str = "account1",
) -> str:
    from core.account_profiles import credentials_hint_for_profile, load_profile

    if isinstance(mode, str):
        mode = ExecMode(mode)
    return credentials_hint_for_profile(load_profile(account_id, mode))
