"""依 EXCHANGE 選擇 Binance / OKX 實作（供 live_runner 等使用）。"""

from __future__ import annotations

from typing import Any

from core.account_profiles import AccountProfile
from core.binance_credentials import ExecMode
from core.exchange_config import is_okx


def profile_configured(profile: AccountProfile) -> bool:
    if is_okx():
        from core.okx_credentials import profile_configured as okx_configured

        return okx_configured(profile)
    from core.account_profiles import profile_configured as binance_configured

    return binance_configured(profile)


def credentials_configured(
    mode: ExecMode | str,
    account_id: str = "account1",
) -> bool:
    if isinstance(mode, str):
        mode = ExecMode(mode)
    from core.account_profiles import load_profile

    return profile_configured(load_profile(account_id, mode))


def credentials_hint(
    mode: ExecMode | str,
    account_id: str = "account1",
) -> str:
    if isinstance(mode, str):
        mode = ExecMode(mode)
    from core.account_profiles import load_profile

    return credentials_hint_for_profile(load_profile(account_id, mode))


def credentials_hint_for_profile(profile: AccountProfile) -> str:
    if is_okx():
        from core.okx_credentials import credentials_hint_for_profile as okx_hint

        return okx_hint(profile)
    from core.account_profiles import credentials_hint_for_profile as binance_hint

    return binance_hint(profile)


def credential_env_names(profile: AccountProfile) -> tuple[str, str, str]:
    """回傳 (key_env, secret_env, passphrase_env)；Binance 第三項為空字串。"""
    if is_okx():
        from core.okx_credentials import credential_env_names as okx_names

        return okx_names(profile)
    from core.account_profiles import credential_env_names as binance_names

    key_env, secret_env = binance_names(profile)
    return key_env, secret_env, ""


def credentials_for_profile(profile: AccountProfile) -> tuple[str, str, str]:
    """回傳 (api_key, api_secret, passphrase)；Binance passphrase 為空。"""
    if is_okx():
        from core.okx_credentials import credentials_for_profile as okx_creds

        return okx_creds(profile)
    from core.account_profiles import credentials_for_profile as binance_creds

    key, secret = binance_creds(profile)
    return key, secret, ""


def exchange_label() -> str:
    return "OKX" if is_okx() else "Binance"


def futures_settings_from_profile(
    profile: AccountProfile,
    *,
    leverage: int = 10,
    total_capital: float | None = None,
    position_pct: float | None = None,
) -> Any:
    if is_okx():
        from core.okx_futures import OkxFuturesSettings

        return OkxFuturesSettings.from_profile(
            profile,
            leverage=leverage,
            total_capital=total_capital,
            position_pct=position_pct,
        )
    from core.binance_futures import FuturesSettings

    return FuturesSettings.from_profile(
        profile,
        leverage=leverage,
        total_capital=total_capital,
        position_pct=position_pct,
    )


def format_exchange_error(exc: Exception) -> str:
    if is_okx():
        from core.okx_futures import format_okx_error

        return format_okx_error(exc)
    from core.binance_futures import format_binance_error

    return format_binance_error(exc)


def verify_futures_connection(settings: Any) -> bool:
    if is_okx():
        from core.okx_futures import verify_connection as okx_verify

        return okx_verify(settings)
    from core.binance_futures import verify_connection as binance_verify

    return binance_verify(settings)
