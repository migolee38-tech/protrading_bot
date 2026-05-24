"""應用程式登入閘道（密碼存於環境變數，勿提交 Git）。"""

from __future__ import annotations

import hmac
import os
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_ENV_PATH, override=False)

_SESSION_KEY = "_auth_ok"


def _env(key: str, default: str = "") -> str:
    val = os.environ.get(key, default)
    return val.strip() if isinstance(val, str) else default


def _secret_or_env(key: str, default: str = "") -> str:
    try:
        if key in st.secrets:
            return str(st.secrets[key]).strip()
    except Exception:
        pass
    return _env(key, default)


def auth_is_enabled() -> bool:
    """有設定 APP_LOGIN_PASSWORD 時啟用登入。"""
    return bool(_secret_or_env("APP_LOGIN_PASSWORD"))


def expected_username() -> str:
    return _secret_or_env("APP_LOGIN_USER", "admin") or "admin"


def expected_password() -> str:
    return _secret_or_env("APP_LOGIN_PASSWORD")


def is_authenticated() -> bool:
    return bool(st.session_state.get(_SESSION_KEY))


def logout() -> None:
    st.session_state.pop(_SESSION_KEY, None)


def _verify(username: str, password: str) -> bool:
    user_ok = hmac.compare_digest(
        (username or "").strip().encode("utf-8"),
        expected_username().encode("utf-8"),
    )
    pwd_ok = hmac.compare_digest(
        (password or "").encode("utf-8"),
        expected_password().encode("utf-8"),
    )
    return user_ok and pwd_ok


def render_login_gate() -> bool:
    """
    未登入時顯示登入表單並回傳 False；已登入回傳 True。
    未設定 APP_LOGIN_PASSWORD 時直接放行（僅建議本機開發）。
    """
    if not auth_is_enabled():
        return True
    if is_authenticated():
        return True

    st.title("量化交易工作站")
    st.caption("此站台已啟用密碼保護，請登入後繼續。")

    with st.form("app_login_form", clear_on_submit=False):
        user = st.text_input("帳號", value=expected_username(), autocomplete="username")
        pwd = st.text_input("密碼", type="password", autocomplete="current-password")
        submitted = st.form_submit_button("登入", type="primary", use_container_width=True)

    if submitted:
        if _verify(user, pwd):
            st.session_state[_SESSION_KEY] = True
            st.rerun()
        st.error("帳號或密碼錯誤")

    st.info(
        "Zeabur：於服務 **Variables** 設定 `APP_LOGIN_USER`（選用）與 `APP_LOGIN_PASSWORD`。"
        "本機：寫入 `.env`（勿 push）。"
    )
    return False


def render_logout_control() -> None:
    if auth_is_enabled() and is_authenticated():
        if st.sidebar.button("登出", key="app_logout_btn", use_container_width=True):
            logout()
            st.rerun()
