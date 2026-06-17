#!/usr/bin/env python3
"""驗證 OKX API 連線（多帳戶 × demo / live）。"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.env_bootstrap import load_project_env

load_project_env()

# 此腳本專用 OKX；若未設定則預設為 okx
os.environ.setdefault("EXCHANGE", "okx")

from core.account_profiles import (  # noqa: E402
    AccountProfile,
    list_account_ids,
    load_profile,
    runner_profiles,
)
from core.binance_credentials import ExecMode, mode_label  # noqa: E402
from core.exchange_config import active_exchange, is_okx  # noqa: E402
from core.okx_credentials import credentials_hint_for_profile, profile_configured  # noqa: E402
from core.okx_futures import OkxFuturesSettings, verify_connection  # noqa: E402

log = logging.getLogger("verify_okx")


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
    )


def _resolve_profiles(spec: str, *, include_live: bool) -> list[AccountProfile]:
    if spec.lower() == "all":
        profiles = runner_profiles(include_live=include_live)
        return [p for p in profiles if p.network != ExecMode.PAPER]
    if not spec.strip():
        out: list[AccountProfile] = []
        for aid in list_account_ids():
            for net in (ExecMode.TESTNET, ExecMode.LIVE):
                if net == ExecMode.LIVE and not include_live:
                    continue
                out.append(load_profile(aid, net))
        return out
    profiles: list[AccountProfile] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            raise ValueError(f"無效 profile: {part!r}（應為 account_id:network）")
        aid, net = part.split(":", 1)
        profiles.append(load_profile(aid.strip().lower(), net.strip().lower()))
    return profiles


def verify_profiles(profiles: list[AccountProfile]) -> bool:
    ok = True
    for profile in profiles:
        if profile.network == ExecMode.PAPER:
            log.info(f"✅ {profile.display_name} — paper 不需 API")
            continue
        if not profile_configured(profile):
            log.error(f"❌ {profile.display_name} — {credentials_hint_for_profile(profile)}")
            ok = False
            continue
        settings = OkxFuturesSettings.from_profile(profile)
        if verify_connection(settings):
            log.info(f"✅ {profile.display_name}")
        else:
            ok = False
    return ok


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="驗證 OKX Demo / 主網 API 連線")
    p.add_argument(
        "--profiles",
        default="",
        help="account_id:network 逗號分隔，或 all（預設：各帳戶 testnet + live）",
    )
    p.add_argument(
        "--include-live",
        action="store_true",
        help="未指定 --profiles 時也驗證 live（預設只驗證 testnet）",
    )
    return p.parse_args()


def main() -> None:
    _setup_logging()
    args = parse_args()

    if not is_okx():
        log.warning(f"EXCHANGE={active_exchange()}；此腳本建議設 EXCHANGE=okx")

    try:
        profiles = _resolve_profiles(
            args.profiles or "",
            include_live=args.include_live or args.profiles.lower() == "all",
        )
    except ValueError as e:
        log.error(str(e))
        raise SystemExit(2) from e

    if not profiles:
        log.error("沒有可驗證的 profile")
        raise SystemExit(2)

    log.info(f"交易所: {active_exchange()}  驗證 {len(profiles)} 個 profile …\n")
    ok = verify_profiles(profiles)
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
