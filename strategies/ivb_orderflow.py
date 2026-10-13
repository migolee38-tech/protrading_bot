"""
IVB Order Flow — Initial Balance 突破 + Volume Profile 定區 + Absorption proxy。

對齊 Fabio Valentini IVB / DLA 公開框架（OKX K 線 proxy）：
  Direction = NY IB 突破並接受
  Location  = IB fixed VP（POC / VA）回踩區
  Aggression = 高量小振幅吸收 + 順勢收盤確認

預設：BTC/ETH/SOL · IB 30m · NY · personal 風控。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

import config as cfg
from core.volume_profile import VolumeProfileLevels, compute_volume_profile
from risk import TradePlan, build_ivb_trade_plan
from strategies.hunting_funding import (
    OpenPosition,
    SimTrade,
    _sl_hit,
    _tp_hit,
)

_NY = ZoneInfo("America/New_York")
_UTC = ZoneInfo("UTC")


@dataclass(frozen=True)
class Signal:
    bar_index: int
    side: str
    entry: float
    plan: TradePlan
    setup: str = "ivb"


@dataclass
class BarResult:
    bar_index: int
    ts: pd.Timestamp
    close: float
    long_sig: bool
    short_sig: bool
    zone_low: float = 0.0
    zone_high: float = 0.0
    ib_high: float = 0.0
    ib_low: float = 0.0
    poc: float = 0.0
    vah: float = 0.0
    val: float = 0.0
    sl_long: float = 0.0
    sl_short: float = 0.0
    absorption: bool = False


@dataclass
class _SessionState:
    day: date
    ib_high: float
    ib_low: float
    ib_end_idx: int
    vp: VolumeProfileLevels
    bias: str | None = None  # "long" | "short"
    invalidated: bool = False
    entries: int = 0
    last_entry_idx: int = -10_000


def prepare_dataframe(raw: pd.DataFrame) -> pd.DataFrame:
    return raw.copy()


def _time_series(df: pd.DataFrame) -> pd.Series:
    if "datetime" in df.columns:
        ts = pd.to_datetime(df["datetime"], utc=True)
    else:
        ts = pd.to_datetime(df.index, utc=True)
    return ts


def _parse_hhmm(s: str) -> tuple[int, int]:
    hh, mm = (int(x) for x in s.split(":"))
    return hh, mm


def _session_tz():
    return _UTC if str(cfg.IVB_SESSION).upper() == "UTC" else _NY


def _session_open_close(d: date) -> tuple[datetime, datetime]:
    tz = _session_tz()
    if str(cfg.IVB_SESSION).upper() == "UTC":
        oh, om = _parse_hhmm(cfg.IVB_UTC_OPEN)
        ch, cm = _parse_hhmm(cfg.IVB_UTC_CLOSE)
    else:
        oh, om = _parse_hhmm(cfg.IVB_NY_OPEN)
        ch, cm = _parse_hhmm(cfg.IVB_NY_CLOSE)
    return (
        datetime.combine(d, time(oh, om), tzinfo=tz),
        datetime.combine(d, time(ch, cm), tzinfo=tz),
    )


def _bar_local(ts: pd.Timestamp) -> datetime:
    """將 K 線時間轉成策略時段時區。"""
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert(_session_tz()).to_pydatetime()


def _in_trade_window(local_dt: datetime) -> bool:
    """IB 完成後至時段收盤前可交易。"""
    d = local_dt.date()
    open_dt, close_dt = _session_open_close(d)
    ib_end = open_dt + timedelta(minutes=cfg.IVB_MINUTES)
    return ib_end <= local_dt <= close_dt


def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    prev_c = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_c).abs(), (low - prev_c).abs()],
        axis=1,
    ).max(axis=1)
    return tr.rolling(period, min_periods=max(2, period // 2)).mean()


def _is_absorption(row: pd.Series, atr_v: float, vol_ma: float) -> bool:
    if atr_v <= 0 or vol_ma <= 0:
        return False
    rng = float(row["high"]) - float(row["low"])
    vol = float(row["volume"])
    return vol >= cfg.IVB_ABS_VOL_MULT * vol_ma and rng <= cfg.IVB_ABS_RANGE_ATR * atr_v


def _build_ib_for_day(
    df: pd.DataFrame,
    ts: pd.Series,
    day: date,
) -> tuple[int, int, float, float, VolumeProfileLevels] | None:
    """回傳 (ib_start_idx, ib_end_idx_inclusive, ib_high, ib_low, vp)。"""
    open_dt, _ = _session_open_close(day)
    ib_end_dt = open_dt + timedelta(minutes=cfg.IVB_MINUTES)

    idxs: list[int] = []
    for i in range(len(df)):
        local = _bar_local(ts.iloc[i])
        if local.date() != day:
            continue
        # K 線時間戳視為該根開盤時間（與多數交易所一致）
        if open_dt <= local < ib_end_dt:
            idxs.append(i)

    if len(idxs) < max(2, cfg.IVB_MIN_IB_BARS):
        return None

    start, end = idxs[0], idxs[-1]
    window = df.iloc[start : end + 1]
    ib_high = float(window["high"].max())
    ib_low = float(window["low"].min())
    if ib_high <= ib_low:
        return None

    vp = compute_volume_profile(
        window["high"],
        window["low"],
        window["close"],
        window["volume"],
        n_bins=cfg.IVB_VP_BINS,
        value_area_pct=cfg.IVB_VA_PCT,
    )
    if vp is None:
        return None
    return start, end, ib_high, ib_low, vp


def _reload_zone(bias: str, ib_high: float, ib_low: float, vp: VolumeProfileLevels) -> tuple[float, float]:
    """
    突破後回踩敏感區：偏 IB 邊界的窄帶，並與 POC 合流。
    避免把整段 VA 當進場區導致觸價過寬、止損過遠。
    """
    mid = 0.5 * (float(ib_high) + float(ib_low))
    if bias == "long":
        z_hi = float(ib_high)
        # 取 POC 與 IB 中軸較高者，形成靠近 IB high 的 reload 帶
        z_lo = max(float(vp.poc), mid)
        z_lo = min(z_lo, z_hi)
        if z_hi - z_lo < (ib_high - ib_low) * 0.05:
            z_lo = z_hi - max((ib_high - ib_low) * 0.15, z_hi * 0.0005)
        return float(min(z_lo, z_hi)), float(max(z_lo, z_hi))
    z_lo = float(ib_low)
    z_hi = min(float(vp.poc), mid)
    z_hi = max(z_hi, z_lo)
    if z_hi - z_lo < (ib_high - ib_low) * 0.05:
        z_hi = z_lo + max((ib_high - ib_low) * 0.15, z_lo * 0.0005)
    return float(min(z_lo, z_hi)), float(max(z_lo, z_hi))


def _touches_zone(row: pd.Series, z_lo: float, z_hi: float) -> bool:
    pad = cfg.IVB_ZONE_PAD_PCT / 100.0
    lo = z_lo * (1.0 - pad)
    hi = z_hi * (1.0 + pad)
    return float(row["low"]) <= hi and float(row["high"]) >= lo


def compute_bar_results(df: pd.DataFrame) -> list[BarResult]:
    if df is None or len(df) == 0:
        return []

    ts = _time_series(df)
    atr = _atr(df, cfg.IVB_ATR_LEN)
    vol_ma = df["volume"].astype(float).rolling(cfg.IVB_VOL_MA_LEN, min_periods=5).mean()

    results: list[BarResult] = []
    sessions: dict[date, _SessionState] = {}

    for i in range(len(df)):
        row = df.iloc[i]
        local = _bar_local(ts.iloc[i])
        d = local.date()
        close = float(row["close"])
        long_sig = short_sig = False
        zone_lo = zone_hi = 0.0
        ib_h = ib_l = poc = vah = val = 0.0
        sl_long = sl_short = 0.0
        absorbed = False

        st = sessions.get(d)
        if st is None:
            built = _build_ib_for_day(df, ts, d)
            if built is not None:
                _s, ib_end, ib_high, ib_low, vp = built
                # 僅在 IB 走完後建立 session（當前 bar >= ib_end）
                if i >= ib_end:
                    st = _SessionState(
                        day=d,
                        ib_high=ib_high,
                        ib_low=ib_low,
                        ib_end_idx=ib_end,
                        vp=vp,
                    )
                    sessions[d] = st

        if st is not None and i > st.ib_end_idx and _in_trade_window(local) and not st.invalidated:
            ib_h, ib_l = st.ib_high, st.ib_low
            poc, vah, val = st.vp.poc, st.vp.vah, st.vp.val
            atr_v = float(atr.iloc[i]) if pd.notna(atr.iloc[i]) else 0.0
            vma = float(vol_ma.iloc[i]) if pd.notna(vol_ma.iloc[i]) else 0.0
            absorbed = _is_absorption(row, atr_v, vma)

            # Direction：首次收盤接受 IB 外
            if st.bias is None:
                if close > st.ib_high:
                    st.bias = "long"
                elif close < st.ib_low:
                    st.bias = "short"

            if st.bias == "long" and close < st.vp.val:
                st.invalidated = True
            elif st.bias == "short" and close > st.vp.vah:
                st.invalidated = True

            if (
                not st.invalidated
                and st.bias is not None
                and st.entries < cfg.IVB_MAX_ENTRIES_PER_DAY
                and i - st.last_entry_idx >= cfg.IVB_COOLDOWN_BARS
            ):
                z_lo, z_hi = _reload_zone(st.bias, st.ib_high, st.ib_low, st.vp)
                zone_lo, zone_hi = z_lo, z_hi
                touched = _touches_zone(row, z_lo, z_hi)

                if st.bias == "long" and touched:
                    # Model2：吸收後多方收盤，或順勢確認 K
                    bullish = close >= float(row["open"])
                    trigger = (absorbed and bullish) or (
                        bullish and close >= z_lo and float(row["volume"]) >= vma
                    )
                    if trigger and cfg.IVB_ALLOW_LONG:
                        # 止損貼區外側（非整段 VAL）；VAL 僅作 bias 失效
                        stop_ref = min(float(row["low"]), z_lo)
                        if atr_v > 0:
                            stop_ref = min(stop_ref, z_lo - 0.15 * atr_v)
                        plan = build_ivb_trade_plan(
                            "long",
                            close,
                            stop_ref=stop_ref,
                            ib_range=st.ib_high - st.ib_low,
                        )
                        if plan:
                            long_sig = True
                            sl_long = plan.stop
                            st.entries += 1
                            st.last_entry_idx = i

                if st.bias == "short" and touched:
                    bearish = close <= float(row["open"])
                    trigger = (absorbed and bearish) or (
                        bearish and close <= z_hi and float(row["volume"]) >= vma
                    )
                    if trigger and cfg.IVB_ALLOW_SHORT:
                        stop_ref = max(float(row["high"]), z_hi)
                        if atr_v > 0:
                            stop_ref = max(stop_ref, z_hi + 0.15 * atr_v)
                        plan = build_ivb_trade_plan(
                            "short",
                            close,
                            stop_ref=stop_ref,
                            ib_range=st.ib_high - st.ib_low,
                        )
                        if plan:
                            short_sig = True
                            sl_short = plan.stop
                            st.entries += 1
                            st.last_entry_idx = i

        results.append(
            BarResult(
                bar_index=i,
                ts=ts.iloc[i],
                close=close,
                long_sig=long_sig,
                short_sig=short_sig,
                zone_low=zone_lo,
                zone_high=zone_hi,
                ib_high=ib_h,
                ib_low=ib_l,
                poc=poc,
                vah=vah,
                val=val,
                sl_long=sl_long,
                sl_short=sl_short,
                absorption=absorbed,
            )
        )
    return results


def scan_raw_signals(df: pd.DataFrame) -> list[Signal]:
    signals: list[Signal] = []
    for bar in compute_bar_results(df):
        if bar.long_sig and bar.sl_long > 0:
            plan = build_ivb_trade_plan(
                "long",
                bar.close,
                stop_ref=bar.sl_long,
                ib_range=max(bar.ib_high - bar.ib_low, 0.0),
                apply_buffer=False,
            )
            if plan:
                signals.append(
                    Signal(bar_index=bar.bar_index, side="long", entry=bar.close, plan=plan)
                )
        if bar.short_sig and bar.sl_short > 0:
            plan = build_ivb_trade_plan(
                "short",
                bar.close,
                stop_ref=bar.sl_short,
                ib_range=max(bar.ib_high - bar.ib_low, 0.0),
                apply_buffer=False,
            )
            if plan:
                signals.append(
                    Signal(bar_index=bar.bar_index, side="short", entry=bar.close, plan=plan)
                )
    return signals


def _open_leg(direction: str, entry_time: pd.Timestamp, entry: float, sl: float, bar_index: int) -> OpenPosition:
    """personal：r1=1R，r3/r5=protection（預設 2R）。"""
    risk = abs(entry - sl)
    rr2 = cfg.IVB_RR_TP2
    rr_f = cfg.IVB_RR_FINAL
    if direction == "LONG":
        r1 = entry + risk
        r3 = entry + rr2 * risk
        r5 = entry + rr_f * risk
    else:
        r1 = entry - risk
        r3 = entry - rr2 * risk
        r5 = entry - rr_f * risk
    return OpenPosition(
        direction=direction,
        entry_time=entry_time,
        entry_price=entry,
        initial_sl=sl,
        sl=sl,
        r1=r1,
        r3=r3,
        r5=r5,
        entry_bar_index=bar_index,
    )


def _process_ivb_exits(
    pos: OpenPosition, high: float, low: float, tp1_reduce: float
) -> tuple[float, str] | None:
    """1R 減倉後，其餘在 protection（r5）出場；中間 r3 僅用於移停。"""
    while pos.remaining > 1e-9:
        if pos.stage == 0:
            if _sl_hit(pos, low, high):
                pos.realized_r += pos.remaining * (-1.0)
                pos.remaining = 0.0
                return pos.realized_r, "SL"
            if _tp_hit(pos, pos.r1, low, high):
                pos.realized_r += tp1_reduce * 1.0
                pos.remaining = 1.0 - tp1_reduce
                pos.sl = pos.entry_price
                pos.stage = 1
                pos.events.append("1R")
                continue
            break
        if pos.stage == 1:
            if _tp_hit(pos, pos.r3, low, high):
                pos.sl = pos.r1
                pos.stage = 2
                pos.events.append("PROT")
                continue
            if _sl_hit(pos, low, high):
                pos.remaining = 0.0
                return pos.realized_r, "+".join(pos.events + ["BE"])
            break
        if pos.stage == 2:
            if _tp_hit(pos, pos.r5, low, high):
                pos.realized_r += pos.remaining * cfg.IVB_RR_FINAL
                pos.remaining = 0.0
                pos.events.append(f"{cfg.IVB_RR_FINAL:g}R")
                return pos.realized_r, "+".join(pos.events)
            if _sl_hit(pos, low, high):
                pos.realized_r += pos.remaining * 1.0
                pos.remaining = 0.0
                return pos.realized_r, "+".join(pos.events + ["BE"])
            break
        break
    return None


def simulate_trades(
    df: pd.DataFrame,
    results: list[BarResult],
) -> tuple[list[SimTrade], list[Signal], list[OpenPosition]]:
    trades: list[SimTrade] = []
    entry_signals: list[Signal] = []
    open_legs: list[OpenPosition] = []

    for i, bar in enumerate(results):
        row = df.iloc[i]
        still_open: list[OpenPosition] = []
        for leg in open_legs:
            closed = _process_ivb_exits(leg, row["high"], row["low"], cfg.IVB_TP1_REDUCE_PCT)
            if closed is not None:
                pnl_r, result = closed
                trades.append(
                    SimTrade(
                        direction=leg.direction,
                        entry_time=leg.entry_time,
                        entry_price=leg.entry_price,
                        sl=leg.initial_sl,
                        r1=leg.r1,
                        r3=leg.r3,
                        r5=leg.r5,
                        bar_index=leg.entry_bar_index,
                        exit_time=bar.ts,
                        exit_price=float(row["close"]),
                        result=result,
                        pnl_r=pnl_r,
                    )
                )
            else:
                still_open.append(leg)
        open_legs = still_open

        if bar.long_sig and bar.sl_long > 0 and len(open_legs) < cfg.IVB_MAX_CONCURRENT:
            plan = build_ivb_trade_plan(
                "long",
                bar.close,
                stop_ref=bar.sl_long,
                ib_range=max(bar.ib_high - bar.ib_low, 0.0),
                apply_buffer=False,
            )
            if plan:
                entry_signals.append(
                    Signal(bar_index=bar.bar_index, side="long", entry=bar.close, plan=plan)
                )
                open_legs.append(_open_leg("LONG", bar.ts, bar.close, bar.sl_long, bar.bar_index))

        if bar.short_sig and bar.sl_short > 0 and len(open_legs) < cfg.IVB_MAX_CONCURRENT:
            plan = build_ivb_trade_plan(
                "short",
                bar.close,
                stop_ref=bar.sl_short,
                ib_range=max(bar.ib_high - bar.ib_low, 0.0),
                apply_buffer=False,
            )
            if plan:
                entry_signals.append(
                    Signal(bar_index=bar.bar_index, side="short", entry=bar.close, plan=plan)
                )
                open_legs.append(_open_leg("SHORT", bar.ts, bar.close, bar.sl_short, bar.bar_index))

    for leg in open_legs:
        trades.append(
            SimTrade(
                direction=leg.direction,
                entry_time=leg.entry_time,
                entry_price=leg.entry_price,
                sl=leg.initial_sl,
                r1=leg.r1,
                r3=leg.r3,
                r5=leg.r5,
                bar_index=leg.entry_bar_index,
                result="OPEN",
                pnl_r=leg.realized_r,
            )
        )
    return trades, entry_signals, open_legs


def scan_signals(df: pd.DataFrame) -> list[Signal]:
    results = compute_bar_results(df)
    _, entry_signals, _ = simulate_trades(df, results)
    return entry_signals


def run_dashboard_backtest(df: pd.DataFrame) -> dict:
    from core.backtest_pnl import summarize_hunting_pnl

    results = compute_bar_results(df)
    raw_signals = sum(1 for r in results if r.long_sig or r.short_sig)
    trades, _, open_legs = simulate_trades(df, results)
    closed = [t for t in trades if t.result != "OPEN"]
    wins = sum(1 for t in closed if t.pnl_r > 0)
    losses = sum(1 for t in closed if t.pnl_r < 0)
    closed_n = wins + losses
    win_rate = (wins / closed_n) if closed_n else 0.0
    last_close = float(df.iloc[-1]["close"])
    pnl = summarize_hunting_pnl(trades, open_legs, last_close)
    events = [
        f"[{t.bar_index}] open {t.direction.lower()} @ {t.entry_price:.6g} sl={t.sl:.6g}"
        for t in trades
    ]
    for t in closed:
        tag = "stop_loss" if "SL" in t.result else "final_tp"
        events.append(f"[{t.bar_index}] {tag} {t.result} pnl_r={t.pnl_r:.2f}")
    return {
        "signal_count": raw_signals,
        "open_count": len(trades),
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "events": events,
        **pnl.to_dict(),
    }
