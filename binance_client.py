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
    """從 Streamlit Secrets、.env 或環境變數讀取 API 密鑰。"""
    load_dotenv(_ENV_PATH)

    api_key = os.getenv("BINANCE_API_KEY") or os.getenv("API_KEY")
    api_secret = os.getenv("BINANCE_API_SECRET") or os.getenv("API_SECRET")

    if not api_key or not api_secret:
        try:
            import streamlit as st

            if hasattr(st, "secrets"):
                api_key = api_key or st.secrets.get("BINANCE_API_KEY") or st.secrets.get("API_KEY")
                api_secret = (
                    api_secret or st.secrets.get("BINANCE_API_SECRET") or st.secrets.get("API_SECRET")
                )
        except Exception:
            pass

    if not api_key or not api_secret:
        raise ValueError(
            "缺少 API 密鑰。請在 .env 或 Streamlit Cloud Secrets 設定 "
            "BINANCE_API_KEY / BINANCE_API_SECRET（或 API_KEY / API_SECRET）。"
        )
    return api_key.strip(), api_secret.strip()


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
