"""依下單模式（paper / testnet / live）與帳戶 ID 載入 OKX API 金鑰（含 passphrase）。"""

from __future__ import annotations

from core.account_profiles import AccountProfile, load_profile
from core.binance_credentials import ExecMode, mode_label
from core.env_bootstrap import env_value, load_project_env, normalize_credential

load_project_env()

_LEGACY_KEY_MAP: dict[tuple[str, ExecMode], tuple[str, str, str]] = {
    ("account1", ExecMode.LIVE): (
        "OKX_API_KEY",
        "OKX_API_SECRET",
        "OKX_PASSPHRASE",
    ),
    ("account1", ExecMode.TESTNET): (
        "OKX_TESTNET_API_KEY",
        "OKX_TESTNET_API_SECRET",
        "OKX_TESTNET_PASSPHRASE",
    ),
}


def _from_streamlit_secrets(key: str) -> str:
    try:
        import streamlit as st

        if hasattr(st, "secrets") and key in st.secrets:
            return normalize_credential(str(st.secrets[key]))
    except Exception:
        pass
    return ""


def _first_env(*keys: str) -> str:
    for key in keys:
        val = env_value(key)
        if val:
            return val
    return ""


def _env_credential_keys(account_id: str, network: ExecMode) -> tuple[str, str, str]:
    prefix = f"OKX_{account_id.upper()}_{network.value.upper()}"
    return (
        f"{prefix}_API_KEY",
        f"{prefix}_API_SECRET",
        f"{prefix}_PASSPHRASE",
    )


def credential_env_names(profile: AccountProfile) -> tuple[str, str, str]:
    return _env_credential_keys(profile.account_id, profile.network)


def credentials_for_profile(profile: AccountProfile) -> tuple[str, str, str]:
    """回傳 (api_key, api_secret, passphrase)。"""
    if profile.network == ExecMode.PAPER:
        return "", "", ""

    key_env, secret_env, pass_env = _env_credential_keys(
        profile.account_id, profile.network
    )
    api_key = _first_env(key_env) or _from_streamlit_secrets(key_env)
    api_secret = _first_env(secret_env) or _from_streamlit_secrets(secret_env)
    passphrase = _first_env(pass_env) or _from_streamlit_secrets(pass_env)

    legacy = _LEGACY_KEY_MAP.get((profile.account_id, profile.network))
    if legacy:
        lk, ls, lp = legacy
        api_key = api_key or _first_env(lk) or _from_streamlit_secrets(lk)
        api_secret = api_secret or _first_env(ls) or _from_streamlit_secrets(ls)
        passphrase = passphrase or _first_env(lp) or _from_streamlit_secrets(lp)

    return api_key, api_secret, passphrase


def profile_configured(profile: AccountProfile) -> bool:
    if profile.network == ExecMode.PAPER:
        return True
    key, secret, passphrase = credentials_for_profile(profile)
    return bool(key and secret and passphrase)


def credentials_hint_for_profile(profile: AccountProfile) -> str:
    if profile.network == ExecMode.PAPER:
        return f"{profile.label} 本地模擬不需 API 金鑰。"
    key_env, secret_env, pass_env = credential_env_names(profile)
    net = mode_label(profile.network)
    demo = "（Demo Trading API，SDK flag=1）" if profile.network == ExecMode.TESTNET else ""
    return (
        f"請設定 {key_env} / {secret_env} / {pass_env}"
        f"（{profile.label} · {net}{demo}）"
    )


def load_credentials(
    mode: ExecMode | str,
    account_id: str = "account1",
) -> tuple[str, str, str]:
    if isinstance(mode, str):
        mode = ExecMode(mode)
    return credentials_for_profile(load_profile(account_id, mode))


def credentials_configured(
    mode: ExecMode | str,
    account_id: str = "account1",
) -> bool:
    if isinstance(mode, str):
        mode = ExecMode(mode)
    return profile_configured(load_profile(account_id, mode))


def credentials_hint(
    mode: ExecMode | str,
    account_id: str = "account1",
) -> str:
    if isinstance(mode, str):
        mode = ExecMode(mode)
    return credentials_hint_for_profile(load_profile(account_id, mode))
