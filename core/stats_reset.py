"""帳戶績效統計歸零點：隱藏較早成交，保留目前持倉（不刪除 Binance 紀錄）。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from core.account_profiles import AccountProfile

_ROOT = Path(__file__).resolve().parent.parent / "data" / "stats_reset"


def _reset_path(profile: AccountProfile) -> Path:
    _ROOT.mkdir(parents=True, exist_ok=True)
    return _ROOT / f"{profile.profile_id}.json"


def get_stats_reset_at(profile: AccountProfile) -> pd.Timestamp | None:
    path = _reset_path(profile)
    if not path.is_file():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        raw = str(data.get("reset_at", "")).strip()
        if not raw:
            return None
        ts = pd.Timestamp(raw)
        if ts.tz is None:
            ts = ts.tz_localize("UTC")
        else:
            ts = ts.tz_convert("UTC")
        return ts
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return None


def get_stats_reset_label(profile: AccountProfile) -> str:
    ts = get_stats_reset_at(profile)
    if ts is None:
        return ""
    return ts.strftime("%Y-%m-%d %H:%M:%S UTC")


def set_stats_reset_now(profile: AccountProfile) -> str:
    """從此刻起重新計算勝率／獲利因子／成交表；持倉不變。"""
    now = datetime.now(timezone.utc).replace(microsecond=0)
    iso = now.isoformat()
    path = _reset_path(profile)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"reset_at": iso, "profile_id": profile.profile_id}, f, indent=2)
    return iso


def clear_stats_reset(profile: AccountProfile) -> bool:
    path = _reset_path(profile)
    if path.is_file():
        path.unlink()
        return True
    return False


def filter_dataframe_after_reset(
    df: pd.DataFrame,
    time_col: str,
    reset_at: pd.Timestamp,
) -> pd.DataFrame:
    if df is None or df.empty or time_col not in df.columns:
        return df
    ts = pd.to_datetime(df[time_col], utc=True, errors="coerce")
    return df.loc[ts >= reset_at].reset_index(drop=True)
