"""幣安 Spot API 客戶端：從 .env 載入密鑰並測試連線。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from binance.error import ClientError, ServerError
from binance.spot import Spot

_PROJECT_ROOT = Path(__file__).resolve().parent
_ENV_PATH = _PROJECT_ROOT / ".env"


def _load_credentials() -> tuple[str, str]:
    """主網現貨 API 密鑰（live）。"""
    from core.binance_credentials import ExecMode, credentials_hint, load_credentials

    load_dotenv(_ENV_PATH)
    api_key, api_secret = load_credentials(ExecMode.LIVE)
    if not api_key or not api_secret:
        raise ValueError(f"缺少 API 密鑰。{credentials_hint(ExecMode.LIVE)}")
    return api_key, api_secret


def create_client() -> Spot:
    """建立並回傳幣安 Spot 客戶端。"""
    api_key, api_secret = _load_credentials()
    return Spot(api_key=api_key, api_secret=api_secret)


def print_account_balances(client: Spot) -> None:
    """印出帳戶中非零餘額資產。"""
    account = client.account()
    balances = account.get("balances", [])

    print("\n--- 帳戶餘額（非零）---")
    has_balance = False
    for item in balances:
        free = float(item.get("free", 0))
        locked = float(item.get("locked", 0))
        total = free + locked
        if total <= 0:
            continue
        has_balance = True
        asset = item.get("asset", "?")
        print(f"  {asset}: 可用 {free:.8f} | 凍結 {locked:.8f} | 合計 {total:.8f}")

    if not has_balance:
        print("  （目前無非零餘額）")
    print("----------------------\n")


def test_connection() -> bool:
    """
    測試與幣安 API 的連線：ping → 伺服器時間 → 帳戶餘額。
    成功回傳 True，失敗回傳 False（錯誤訊息不含密鑰）。
    """
    try:
        client = create_client()
    except ValueError as exc:
        print(f"[設定錯誤] {exc}", file=sys.stderr)
        return False
    except Exception as exc:
        print(f"[初始化失敗] {type(exc).__name__}: {exc}", file=sys.stderr)
        return False

    try:
        client.ping()
        server_time = client.time()
        print("✓ 連線成功（ping 正常）")
        print(f"  伺服器時間戳: {server_time.get('serverTime')}")

        print_account_balances(client)
        return True

    except ClientError as exc:
        print(
            f"[幣安 API 錯誤] HTTP {exc.status_code} | "
            f"code={exc.error_code} | {exc.error_message}",
            file=sys.stderr,
        )
        return False
    except ServerError as exc:
        print(f"[幣安伺服器錯誤] {exc}", file=sys.stderr)
        return False
    except Exception as exc:
        print(f"[未預期錯誤] {type(exc).__name__}: {exc}", file=sys.stderr)
        return False


if __name__ == "__main__":
    ok = test_connection()
    sys.exit(0 if ok else 1)
