"""網站顯示用時區：統一為台灣時間（Asia/Taipei, UTC+8）。"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

DISPLAY_TZ = ZoneInfo("Asia/Taipei")
DISPLAY_TZ_NAME = "台北時間"


def now_display() -> datetime:
    return datetime.now(DISPLAY_TZ)


def today_display() -> date:
    return now_display().date()


def start_of_day_display(d: date) -> datetime:
    return datetime.combine(d, time.min, tzinfo=DISPLAY_TZ)


def end_of_day_display(d: date) -> datetime:
    return datetime.combine(d, time.max, tzinfo=DISPLAY_TZ)


def to_display_tz(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(DISPLAY_TZ)


def format_datetime_display(dt: datetime, *, with_label: bool = False) -> str:
    text = to_display_tz(dt).strftime("%Y-%m-%d %H:%M:%S")
    if with_label:
        return f"{text} {DISPLAY_TZ_NAME}"
    return text


def format_any_time_display(val: Any, *, with_label: bool = False) -> str:
    """將毫秒、ISO 字串、Timestamp 等轉為台北時間字串。"""
    if val is None:
        return ""
    if isinstance(val, float) and pd.isna(val):
        return ""
    text = str(val).strip()
    if not text or text in ("—", "nan", "NaT"):
        return ""

    try:
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            num = float(val)
            if num > 1e12:
                ts = pd.to_datetime(int(num), unit="ms", utc=True)
            elif num > 1e9:
                ts = pd.to_datetime(int(num), unit="s", utc=True)
            else:
                return text
        else:
            ts = pd.to_datetime(val, utc=True, errors="coerce")
        if pd.isna(ts):
            return text
        return format_datetime_display(ts.to_pydatetime(), with_label=with_label)
    except (TypeError, ValueError, OverflowError):
        return text


def format_ms_display(ms: Any, *, with_label: bool = False) -> str:
    return format_any_time_display(ms, with_label=with_label)
