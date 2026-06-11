"""依下單模式（paper / testnet / live）載入對應的 Binance API 金鑰。"""

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


_MODE_KEYS: dict[ExecMode, tuple[str, str]] = {
    ExecMode.TESTNET: ("BINANCE_TESTNET_API_KEY", "BINANCE_TESTNET_API_SECRET"),
    ExecMode.LIVE: ("BINANCE_API_KEY", "BINANCE_API_SECRET"),
}


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


def _from_streamlit_secrets(key: str) -> str:
    try:
        import streamlit as st

        if hasattr(st, "secrets") and key in st.secrets:
            return str(st.secrets[key]).strip()
    except Exception:
        pass
    return ""


def load_credentials(mode: ExecMode | str) -> tuple[str, str]:
    """
    依模式回傳 (api_key, api_secret)。
    paper 回傳空字串；testnet / live 從對應環境變數讀取。
    """
    if isinstance(mode, str):
        mode = ExecMode(mode)

    if mode == ExecMode.PAPER:
        return "", ""

    key_env, secret_env = _MODE_KEYS[mode]
    api_key = os.getenv(key_env, "").strip()
    api_secret = os.getenv(secret_env, "").strip()

    api_key = api_key or _from_streamlit_secrets(key_env)
    api_secret = api_secret or _from_streamlit_secrets(secret_env)

    # Testnet 相容舊設定：未設 TESTNET 專用變數時 fallback 至 BINANCE_API_KEY
    if mode == ExecMode.TESTNET and (not api_key or not api_secret):
        api_key = api_key or os.getenv("BINANCE_API_KEY", "").strip()
        api_secret = api_secret or os.getenv("BINANCE_API_SECRET", "").strip()
        api_key = api_key or _from_streamlit_secrets("BINANCE_API_KEY")
        api_secret = api_secret or _from_streamlit_secrets("BINANCE_API_SECRET")

    # 通用別名（僅 live；testnet 已在上方 fallback）
    if mode == ExecMode.LIVE:
        api_key = api_key or os.getenv("API_KEY", "").strip()
        api_secret = api_secret or os.getenv("API_SECRET", "").strip()
        api_key = api_key or _from_streamlit_secrets("API_KEY")
        api_secret = api_secret or _from_streamlit_secrets("API_SECRET")

    return api_key, api_secret


def credentials_configured(mode: ExecMode | str) -> bool:
    if isinstance(mode, str):
        mode = ExecMode(mode)
    if mode == ExecMode.PAPER:
        return True
    key, secret = load_credentials(mode)
    return bool(key and secret)


def credentials_hint(mode: ExecMode | str) -> str:
    if isinstance(mode, str):
        mode = ExecMode(mode)
    if mode == ExecMode.PAPER:
        return "本地模擬不需 API 金鑰。"
    if mode == ExecMode.TESTNET:
        return (
            "請在 .env 設定 BINANCE_TESTNET_API_KEY / BINANCE_TESTNET_API_SECRET"
            "（https://testnet.binancefuture.com 申請）"
        )
    return "請在 .env 設定 BINANCE_API_KEY / BINANCE_API_SECRET（主網金鑰）。"
