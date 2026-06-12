"""專案環境變數載入：Zeabur / 雲端部署時不讓本機 .env 覆蓋平台 Variables。"""

from __future__ import annotations

import os
import re
from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_PATH = _PROJECT_ROOT / ".env"
_LOADED = False

_QUOTE_WRAP = re.compile(r'^["\'](.+)["\']$')


def is_managed_deploy() -> bool:
    """Zeabur、Streamlit Cloud 等由平台注入環境變數的部署。"""
    return bool(
        os.environ.get("ZEABUR")
        or os.environ.get("ZEABUR_ENV_ID")
        or os.environ.get("STREAMLIT_SHARING_MODE")
    )


def load_project_env(*, force: bool = False) -> None:
    global _LOADED
    if _LOADED and not force:
        return
    if _ENV_PATH.is_file():
        load_dotenv(_ENV_PATH, override=not is_managed_deploy())
    _LOADED = True


def normalize_credential(value: str) -> str:
    """去除 Zeabur / .env 常見的引號、空白、換行。"""
    s = (value or "").strip().strip("\ufeff")
    if not s:
        return ""
    m = _QUOTE_WRAP.match(s)
    if m:
        s = m.group(1).strip()
    return s.replace("\r", "").replace("\n", "")


def env_value(key: str, default: str = "") -> str:
    load_project_env()
    return normalize_credential(os.environ.get(key, default))


def credential_status(key: str) -> dict[str, str | int | bool]:
    """供 UI 診斷：不洩漏金鑰內容。"""
    raw = os.environ.get(key, "")
    normalized = normalize_credential(raw)
    return {
        "key": key,
        "present": bool(raw),
        "configured": bool(normalized),
        "length": len(normalized),
    }
