"""固定區間 Volume Profile：POC / Value Area（約 70% 成交量）。"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class VolumeProfileLevels:
    poc: float
    vah: float
    val: float
    price_min: float
    price_max: float
    total_volume: float


def compute_volume_profile(
    high: pd.Series | np.ndarray,
    low: pd.Series | np.ndarray,
    close: pd.Series | np.ndarray,
    volume: pd.Series | np.ndarray,
    *,
    n_bins: int = 24,
    value_area_pct: float = 0.70,
) -> VolumeProfileLevels | None:
    """
    將每根 K 的成交量均勻分配到 [low, high] 重疊的價位 bin，
    回傳 POC 與涵蓋 value_area_pct 成交量的 VAH/VAL。
    """
    h = np.asarray(high, dtype=float)
    l = np.asarray(low, dtype=float)
    c = np.asarray(close, dtype=float)
    v = np.asarray(volume, dtype=float)
    if len(h) == 0 or np.nansum(v) <= 0:
        return None

    p_min = float(np.nanmin(l))
    p_max = float(np.nanmax(h))
    if not np.isfinite(p_min) or not np.isfinite(p_max):
        return None
    if p_max <= p_min:
        mid = float(c[-1]) if len(c) else p_min
        return VolumeProfileLevels(
            poc=mid, vah=mid, val=mid, price_min=p_min, price_max=p_max, total_volume=float(np.nansum(v))
        )

    n_bins = max(8, int(n_bins))
    edges = np.linspace(p_min, p_max, n_bins + 1)
    centers = 0.5 * (edges[:-1] + edges[1:])
    vol_bins = np.zeros(n_bins, dtype=float)

    for hi, lo, vol in zip(h, l, v):
        if not np.isfinite(hi) or not np.isfinite(lo) or not np.isfinite(vol) or vol <= 0:
            continue
        lo_i = float(min(lo, hi))
        hi_i = float(max(lo, hi))
        span = hi_i - lo_i
        if span <= 0:
            # 單價位：丟進最近 bin
            idx = int(np.clip(np.searchsorted(edges, lo_i, side="right") - 1, 0, n_bins - 1))
            vol_bins[idx] += vol
            continue
        for b in range(n_bins):
            b_lo, b_hi = edges[b], edges[b + 1]
            overlap = max(0.0, min(hi_i, b_hi) - max(lo_i, b_lo))
            if overlap > 0:
                vol_bins[b] += vol * (overlap / span)

    total = float(vol_bins.sum())
    if total <= 0:
        mid = float(np.nanmean(c))
        return VolumeProfileLevels(
            poc=mid, vah=mid, val=mid, price_min=p_min, price_max=p_max, total_volume=0.0
        )

    poc_idx = int(np.argmax(vol_bins))
    poc = float(centers[poc_idx])

    target = total * float(value_area_pct)
    lo_i = hi_i = poc_idx
    covered = vol_bins[poc_idx]
    while covered < target and (lo_i > 0 or hi_i < n_bins - 1):
        left = vol_bins[lo_i - 1] if lo_i > 0 else -1.0
        right = vol_bins[hi_i + 1] if hi_i < n_bins - 1 else -1.0
        if right >= left:
            hi_i += 1
            covered += vol_bins[hi_i]
        else:
            lo_i -= 1
            covered += vol_bins[lo_i]

    val = float(edges[lo_i])
    vah = float(edges[hi_i + 1])
    return VolumeProfileLevels(
        poc=poc,
        vah=vah,
        val=val,
        price_min=p_min,
        price_max=p_max,
        total_volume=total,
    )
