"""多帳戶 Profile：account_id × network（live / testnet / paper）。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from core.binance_credentials import ExecMode, mode_label
from core.env_bootstrap import env_value, load_project_env, normalize_credential

load_project_env()

_DEFAULT_LABELS = {"account1": "帳戶 1", "account2": "帳戶 2"}

_LEGACY_KEY_MAP: dict[tuple[str, ExecMode], tuple[str, str]] = {
    ("account1", ExecMode.LIVE): ("BINANCE_API_KEY", "BINANCE_API_SECRET"),
    ("account1", ExecMode.TESTNET): (
        "BINANCE_TESTNET_API_KEY",
        "BINANCE_TESTNET_API_SECRET",
    ),
    ("account2", ExecMode.LIVE): (
        "BINANCE_ACCOUNT2_API_KEY",
        "BINANCE_ACCOUNT2_API_SECRET",
    ),
}

# 常見誤設的變數名（依序嘗試，僅補主欄位為空時）
_CREDENTIAL_ALIASES: dict[tuple[str, ExecMode], list[tuple[str, str]]] = {
    ("account2", ExecMode.TESTNET): [
        ("BINANCE_ACCOUNT2_TESTNET_KEY", "BINANCE_ACCOUNT2_TESTNET_SECRET"),
    ],
    ("account2", ExecMode.LIVE): [
        ("BINANCE_ACCOUNT2_LIVE_API_KEY", "BINANCE_ACCOUNT2_LIVE_API_SECRET"),
    ],
}


@dataclass(frozen=True)
class AccountProfile:
    account_id: str
    label: str
    network: ExecMode

    @property
    def profile_id(self) -> str:
        return f"{self.account_id}_{self.network.value}"

    @property
    def display_name(self) -> str:
        return f"{self.label} · {mode_label(self.network)}"


def _from_streamlit_secrets(key: str) -> str:
    try:
        import streamlit as st

        if hasattr(st, "secrets") and key in st.secrets:
            return normalize_credential(str(st.secrets[key]))
    except Exception:
        pass
    return ""


def _first_env(*keys: str) -> str:
    for k in keys:
        val = env_value(k)
        if val:
            return val
    return ""


def credential_env_names(profile: AccountProfile) -> tuple[str, str]:
    return _env_credential_keys(profile.account_id, profile.network)


def list_account_ids() -> list[str]:
    raw = os.getenv("TRADING_ACCOUNTS", "account1,account2").strip()
    ids = [x.strip().lower() for x in raw.split(",") if x.strip()]
    return ids or ["account1"]


def account_label(account_id: str) -> str:
    env_key = f"{account_id.upper()}_LABEL"
    return os.getenv(env_key, _DEFAULT_LABELS.get(account_id, account_id))


def load_profile(account_id: str, network: ExecMode | str) -> AccountProfile:
    if isinstance(network, str):
        network = ExecMode(network)
    aid = account_id.strip().lower()
    return AccountProfile(account_id=aid, label=account_label(aid), network=network)


def profile_from_id(profile_id: str) -> AccountProfile:
    account_id, network = profile_id.rsplit("_", 1)
    return load_profile(account_id, network)


def list_profiles(*, network: ExecMode | str | None = None) -> list[AccountProfile]:
    net_filter: ExecMode | None = None
    if network is not None:
        net_filter = ExecMode(network) if isinstance(network, str) else network
    profiles: list[AccountProfile] = []
    for aid in list_account_ids():
        for net in ExecMode:
            if net_filter is not None and net != net_filter:
                continue
            profiles.append(load_profile(aid, net))
    return profiles


def _env_credential_keys(account_id: str, network: ExecMode) -> tuple[str, str]:
    prefix = f"BINANCE_{account_id.upper()}_{network.value.upper()}"
    return f"{prefix}_API_KEY", f"{prefix}_API_SECRET"


def credentials_for_profile(profile: AccountProfile) -> tuple[str, str]:
    if profile.network == ExecMode.PAPER:
        return "", ""

    key_env, secret_env = _env_credential_keys(profile.account_id, profile.network)
    api_key = _first_env(key_env) or _from_streamlit_secrets(key_env)
    api_secret = _first_env(secret_env) or _from_streamlit_secrets(secret_env)

    for ak, sk in _CREDENTIAL_ALIASES.get(
        (profile.account_id, profile.network), []
    ):
        api_key = api_key or _first_env(ak) or _from_streamlit_secrets(ak)
        api_secret = api_secret or _first_env(sk) or _from_streamlit_secrets(sk)

    legacy = _LEGACY_KEY_MAP.get((profile.account_id, profile.network))
    if legacy:
        lk, ls = legacy
        api_key = api_key or _first_env(lk) or _from_streamlit_secrets(lk)
        api_secret = api_secret or _first_env(ls) or _from_streamlit_secrets(ls)

    if profile.network == ExecMode.TESTNET and profile.account_id == "account1":
        if not api_key or not api_secret:
            api_key = api_key or _first_env("BINANCE_API_KEY")
            api_secret = api_secret or _first_env("BINANCE_API_SECRET")

    if profile.network == ExecMode.LIVE:
        api_key = api_key or _first_env("API_KEY") or _from_streamlit_secrets("API_KEY")
        api_secret = (
            api_secret or _first_env("API_SECRET") or _from_streamlit_secrets("API_SECRET")
        )

    return api_key, api_secret


def profile_configured(profile: AccountProfile) -> bool:
    if profile.network == ExecMode.PAPER:
        return True
    key, secret = credentials_for_profile(profile)
    return bool(key and secret)


def credentials_hint_for_profile(profile: AccountProfile) -> str:
    if profile.network == ExecMode.PAPER:
        return f"{profile.label} 本地模擬不需 API 金鑰。"
    key_env, secret_env = _env_credential_keys(profile.account_id, profile.network)
    net = mode_label(profile.network)
    return f"請設定 {key_env} / {secret_env}（{profile.label} · {net}）"


def profile_capital(profile: AccountProfile) -> float:
    specific = os.getenv(f"{profile.account_id.upper()}_CAPITAL", "").strip()
    if specific:
        return float(specific)
    return float(os.getenv("LIVE_TOTAL_CAPITAL", "1000"))


def profile_position_pct(profile: AccountProfile) -> float:
    specific = os.getenv(f"{profile.account_id.upper()}_POSITION_PCT", "").strip()
    if specific:
        return float(specific)
    return float(os.getenv("LIVE_POSITION_PCT", "1"))


def parse_runner_profile_spec(spec: str) -> AccountProfile:
    part = spec.strip()
    if ":" not in part:
        raise ValueError(f"無效的 profile 規格: {spec!r}（應為 account_id:network）")
    aid, net = part.split(":", 1)
    return load_profile(aid.strip().lower(), net.strip().lower())


def runner_profiles(*, include_live: bool = False) -> list[AccountProfile]:
    """
    自動交易輪詢的 profile 清單。
    RUNNER_PROFILES=account1:testnet,account2:live,account1:paper
    未設定時：各帳戶 paper + 已設定金鑰的 testnet（不含 live，除非 include_live）。
    """
    raw = os.getenv("RUNNER_PROFILES", "").strip()
    if raw:
        if raw.lower() == "all":
            return _default_runner_profiles(include_live=True)
        return [parse_runner_profile_spec(s) for s in raw.split(",") if s.strip()]
    return _default_runner_profiles(include_live=include_live)


def _default_runner_profiles(*, include_live: bool) -> list[AccountProfile]:
    out: list[AccountProfile] = []
    for aid in list_account_ids():
        for net in (ExecMode.PAPER, ExecMode.TESTNET, ExecMode.LIVE):
            if net == ExecMode.LIVE and not include_live:
                continue
            profile = load_profile(aid, net)
            if net == ExecMode.PAPER or profile_configured(profile):
                out.append(profile)
    return out


def orders_file_for_profile(profile: AccountProfile) -> Path:
    base = Path(__file__).resolve().parent.parent / "data" / "orders"
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{profile.profile_id}.json"


def state_file_for_profile(profile: AccountProfile) -> Path:
    base = Path(__file__).resolve().parent.parent / "data" / "live_trader_state"
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{profile.profile_id}.json"


def live_positions_file_for_profile(profile: AccountProfile) -> Path:
    base = Path(__file__).resolve().parent.parent / "data" / "live_positions"
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{profile.profile_id}.json"


_LEGACY_ORDERS = Path(__file__).resolve().parent.parent / "data" / "paper_orders.json"
