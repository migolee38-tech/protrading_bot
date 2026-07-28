"""
Hunting 2.0 — Pine「Hunting Funding」更新版轉寫。
五星合流（OI/CVD/量能/趨勢/動能）+ EMA150 趨勢閘門 + 4H 收盤突破閘門 + SL buffer。
供儀表板、回測與 live_runner 共用；出場沿用 1R 減倉 / 3R 移 SL / 5R 全出。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

import config as cfg
from core.market_data import fetch_open_interest_history
from risk import build_hunting2_trade_plan
from strategies.hunting_funding import (
    OI_STATUS_ATTR,
    OpenPosition,
    Signal,
    SimTrade,
    _build_oi_status,
    _process_bar_exits,
    calc_cvd,
    calc_ema,
    calc_exit_levels,
    load_oi_status,
)

__all__ = [
    "BarResult",
    "Signal",
    "prepare_dataframe",
    "scan_signals",
    "scan_raw_signals",
    "compute_bar_results",
    "run_dashboard_backtest",
    "load_oi_status",
]


@dataclass
class BarResult:
    bar_index: int
    ts: pd.Timestamp
    close: float
    stars_l: int
    stars_s: int
    sl_long: float
    sl_short: float
    sl_pct_long: float
    sl_pct_short: float
    long_sig: bool
    short_sig: bool
    h4_high_close: float = np.nan
    h4_low_close: float = np.nan


@dataclass
class DirectionCooldownState:
    long_blocked: bool = False
    short_blocked: bool = False
    consecutive_long_sl: int = 0
    consecutive_short_sl: int = 0


def _time_series(df: pd.DataFrame) -> pd.Series:
    if "datetime" in df.columns:
        return pd.to_datetime(df["datetime"], utc=True)
    return pd.to_datetime(df.index, utc=True)


def calc_h4_close_levels(
    df: pd.DataFrame,
    h4_bars: int,
) -> tuple[pd.Series, pd.Series]:
    """
    對齊 Pine request.security(…, \"240\", ta.highest/lowest(close[1], N))。
    取已收完 4H 棒的前 N 根最高/最低收盤，再 ffill 回圖表週期（lookahead_off）。
    """
    ts = _time_series(df)
    work = pd.Series(df["close"].astype(float).values, index=pd.DatetimeIndex(ts))
    h4_close = work.resample("4h").last()
    # close[1] 視角：不含當根已收 4H，用 shift(1) 後再 rolling
    prev = h4_close.shift(1)
    h4_high = prev.rolling(h4_bars, min_periods=h4_bars).max()
    h4_low = prev.rolling(h4_bars, min_periods=h4_bars).min()
    high_aligned = h4_high.reindex(work.index, method="ffill")
    low_aligned = h4_low.reindex(work.index, method="ffill")
    high_aligned.index = df.index
    low_aligned.index = df.index
    return high_aligned, low_aligned


def prepare_dataframe(raw: pd.DataFrame) -> pd.DataFrame:
    """合併 OI 欄位；需 raw.attrs['symbol']，可選 raw.attrs['kline_limit']。"""
    out = raw.copy()
    symbol = str(out.attrs.get("symbol", "BTCUSDT")).replace("/", "").upper()
    kline_limit = int(out.attrs.get("kline_limit", len(out)))
    interval = cfg.HUNTING2_TIMEFRAME
    ts = _time_series(out)

    fetch = fetch_open_interest_history(symbol, interval, limit=cfg.HUNTING2_OI_MAX_HIST)
    if not fetch.ok or fetch.series.empty:
        out["oi"] = np.nan
    else:
        aligned = fetch.series.reindex(pd.DatetimeIndex(ts), method="ffill")
        out["oi"] = aligned.values

    out.attrs[OI_STATUS_ATTR] = _build_oi_status(out, fetch, symbol, kline_limit)
    return out


class Hunting2Engine:
    def compute(self, df: pd.DataFrame) -> list[BarResult]:
        n = len(df)
        cvd = calc_cvd(df)
        vol_ma = df["volume"].rolling(cfg.HUNTING2_VOL_LEN).mean()
        htf_ema = calc_ema(df["close"], cfg.HUNTING2_HTF_EMA_LEN)
        ema_trend = calc_ema(df["close"], cfg.HUNTING2_TREND_EMA_LEN)
        h4_high, h4_low = calc_h4_close_levels(df, cfg.HUNTING2_H4_BARS)
        ts = _time_series(df)

        if "oi" in df.columns:
            oi_aligned = pd.Series(df["oi"].values, index=df.index)
        else:
            oi_aligned = pd.Series(np.nan, index=df.index)

        win = cfg.HUNTING2_SL_SWING + 1
        swing_low = df["low"].rolling(win).min()
        swing_high = df["high"].rolling(win).max()
        buf = cfg.HUNTING2_SL_BUFFER_PCT / 100.0

        results: list[BarResult] = []
        last_bar_l: Optional[int] = None
        last_bar_s: Optional[int] = None

        for i in range(n):
            close_i = float(df["close"].iloc[i])
            htf_i = htf_ema.iloc[i]
            trend_i = ema_trend.iloc[i]
            h4h = h4_high.iloc[i]
            h4l = h4_low.iloc[i]

            if not np.isnan(htf_i) and htf_i != 0:
                dist_pct = abs(close_i - htf_i) / htf_i * 100
            else:
                dist_pct = np.nan

            bull_htf = (not cfg.HUNTING2_USE_HTF) or (
                not np.isnan(htf_i) and close_i > htf_i
            )
            bear_htf = (not cfg.HUNTING2_USE_HTF) or (
                not np.isnan(htf_i) and close_i < htf_i
            )
            near_ema = (not cfg.HUNTING2_USE_HTF) or (
                not np.isnan(dist_pct) and dist_pct <= cfg.HUNTING2_MAX_DIST_PCT
            )

            gate4h_l = (not cfg.HUNTING2_USE_4H) or (
                not np.isnan(h4h) and close_i > float(h4h)
            )
            gate4h_s = (not cfg.HUNTING2_USE_4H) or (
                not np.isnan(h4l) and close_i < float(h4l)
            )

            lb = cfg.HUNTING2_LOOKBACK
            oi_i = oi_aligned.iloc[i]
            oi_prev = oi_aligned.iloc[i - lb] if i >= lb else np.nan
            oi_avail = not np.isnan(oi_i) and not np.isnan(oi_prev)
            oi_chg = (oi_i / oi_prev - 1) * 100 if oi_avail else np.nan
            oi_move = oi_avail and abs(oi_chg) >= cfg.HUNTING2_OI_MIN_PCT

            cvd_i = cvd.iloc[i]
            cvd_prev = cvd.iloc[i - lb] if i >= lb else np.nan
            vol_i = df["volume"].iloc[i]
            volma_i = vol_ma.iloc[i]
            close_mom = (
                df["close"].iloc[i - cfg.HUNTING2_MOM_LEN]
                if i >= cfg.HUNTING2_MOM_LEN
                else np.nan
            )

            f_oi = cfg.HUNTING2_USE_OI and oi_move
            f_vol = cfg.HUNTING2_USE_VOL and not np.isnan(volma_i) and vol_i > volma_i
            f_cvd_l = cfg.HUNTING2_USE_CVD and not np.isnan(cvd_prev) and cvd_i > cvd_prev
            f_cvd_s = cfg.HUNTING2_USE_CVD and not np.isnan(cvd_prev) and cvd_i < cvd_prev
            f_trend_l = (
                cfg.HUNTING2_USE_TREND and not np.isnan(trend_i) and close_i > trend_i
            )
            f_trend_s = (
                cfg.HUNTING2_USE_TREND and not np.isnan(trend_i) and close_i < trend_i
            )
            f_mom_l = (
                cfg.HUNTING2_USE_MOM and not np.isnan(close_mom) and close_i > close_mom
            )
            f_mom_s = (
                cfg.HUNTING2_USE_MOM and not np.isnan(close_mom) and close_i < close_mom
            )

            max_score = (
                (cfg.HUNTING2_W_OI if cfg.HUNTING2_USE_OI and oi_avail else 0)
                + (cfg.HUNTING2_W_CVD if cfg.HUNTING2_USE_CVD else 0)
                + (cfg.HUNTING2_W_VOL if cfg.HUNTING2_USE_VOL else 0)
                + (cfg.HUNTING2_W_TREND if cfg.HUNTING2_USE_TREND else 0)
                + (cfg.HUNTING2_W_MOM if cfg.HUNTING2_USE_MOM else 0)
            )
            raw_l = (
                (cfg.HUNTING2_W_OI if f_oi else 0)
                + (cfg.HUNTING2_W_CVD if f_cvd_l else 0)
                + (cfg.HUNTING2_W_VOL if f_vol else 0)
                + (cfg.HUNTING2_W_TREND if f_trend_l else 0)
                + (cfg.HUNTING2_W_MOM if f_mom_l else 0)
            )
            raw_s = (
                (cfg.HUNTING2_W_OI if f_oi else 0)
                + (cfg.HUNTING2_W_CVD if f_cvd_s else 0)
                + (cfg.HUNTING2_W_VOL if f_vol else 0)
                + (cfg.HUNTING2_W_TREND if f_trend_s else 0)
                + (cfg.HUNTING2_W_MOM if f_mom_s else 0)
            )

            stars_l = int(round(raw_l / max_score * 5)) if max_score > 0 else 0
            stars_s = int(round(raw_s / max_score * 5)) if max_score > 0 else 0

            prev_stars_l = results[-1].stars_l if results else 0
            prev_stars_s = results[-1].stars_s if results else 0
            cross_l = stars_l >= cfg.HUNTING2_MIN_STARS and prev_stars_l < cfg.HUNTING2_MIN_STARS
            cross_s = stars_s >= cfg.HUNTING2_MIN_STARS and prev_stars_s < cfg.HUNTING2_MIN_STARS

            can_fire_l = last_bar_l is None or (i - last_bar_l) >= cfg.HUNTING2_COOLDOWN_BARS
            can_fire_s = last_bar_s is None or (i - last_bar_s) >= cfg.HUNTING2_COOLDOWN_BARS

            raw_sl_l = swing_low.iloc[i]
            raw_sl_s = swing_high.iloc[i]
            sl_long = (
                float(raw_sl_l) * (1.0 - buf) if not np.isnan(raw_sl_l) else np.nan
            )
            sl_short = (
                float(raw_sl_s) * (1.0 + buf) if not np.isnan(raw_sl_s) else np.nan
            )
            sl_pct_l = (
                (close_i - sl_long) / close_i * 100
                if close_i and not np.isnan(sl_long)
                else np.nan
            )
            sl_pct_s = (
                (sl_short - close_i) / close_i * 100
                if close_i and not np.isnan(sl_short)
                else np.nan
            )
            sl_ok_l = not np.isnan(sl_pct_l) and sl_pct_l <= cfg.HUNTING2_MAX_SL_PCT
            sl_ok_s = not np.isnan(sl_pct_s) and sl_pct_s <= cfg.HUNTING2_MAX_SL_PCT

            long_sig = (
                cross_l
                and bull_htf
                and near_ema
                and sl_ok_l
                and can_fire_l
                and gate4h_l
            )
            short_sig = (
                cross_s
                and bear_htf
                and near_ema
                and sl_ok_s
                and can_fire_s
                and gate4h_s
            )

            if long_sig:
                last_bar_l = i
            if short_sig:
                last_bar_s = i

            results.append(
                BarResult(
                    bar_index=i,
                    ts=pd.Timestamp(ts.iloc[i]),
                    close=close_i,
                    stars_l=stars_l,
                    stars_s=stars_s,
                    sl_long=float(sl_long) if not np.isnan(sl_long) else np.nan,
                    sl_short=float(sl_short) if not np.isnan(sl_short) else np.nan,
                    sl_pct_long=float(sl_pct_l) if not np.isnan(sl_pct_l) else np.nan,
                    sl_pct_short=float(sl_pct_s) if not np.isnan(sl_pct_s) else np.nan,
                    long_sig=long_sig,
                    short_sig=short_sig,
                    h4_high_close=float(h4h) if not np.isnan(h4h) else np.nan,
                    h4_low_close=float(h4l) if not np.isnan(h4l) else np.nan,
                )
            )
        return results


def _margin_per_leg() -> float:
    return cfg.HUNTING2_TOTAL_CAPITAL * cfg.HUNTING2_POSITION_PCT / 100.0


def _can_open_more_legs(open_count: int) -> bool:
    if open_count >= cfg.HUNTING2_MAX_CONCURRENT_POSITIONS:
        return False
    per = _margin_per_leg()
    used = open_count * per
    cap = cfg.HUNTING2_TOTAL_CAPITAL * cfg.HUNTING2_MAX_MARGIN_USAGE_PCT / 100.0
    return used + per <= cap + 1e-9


def _open_position(
    direction: str,
    entry_time: pd.Timestamp,
    entry: float,
    sl: float,
    bar_index: int,
) -> OpenPosition:
    lv = calc_exit_levels(entry, sl, direction)
    return OpenPosition(
        direction=direction,
        entry_time=entry_time,
        entry_price=entry,
        initial_sl=sl,
        sl=sl,
        r1=lv["r1"],
        r3=lv["r3"],
        r5=lv["r5"],
        entry_bar_index=bar_index,
    )


def _try_open_leg(
    bar: BarResult,
    side: str,
    sl: float,
    stars: int,
    open_legs: list[OpenPosition],
    entry_signals: list[Signal],
) -> None:
    if not _can_open_more_legs(len(open_legs)):
        return
    plan = build_hunting2_trade_plan(side, bar.close, sl)
    if not plan:
        return
    direction = "LONG" if side == "long" else "SHORT"
    entry_signals.append(
        Signal(bar_index=bar.bar_index, side=side, entry=bar.close, plan=plan, stars=stars)
    )
    open_legs.append(_open_position(direction, bar.ts, bar.close, sl, bar.bar_index))


def _unlock_on_opposite_signal(state: DirectionCooldownState, bar: BarResult) -> None:
    if bar.short_sig:
        state.long_blocked = False
        state.consecutive_long_sl = 0
    if bar.long_sig:
        state.short_blocked = False
        state.consecutive_short_sl = 0


def _record_direction_close(state: DirectionCooldownState, direction: str, result: str) -> None:
    is_sl = result == "SL"
    if direction == "LONG":
        if is_sl:
            state.consecutive_long_sl += 1
            if state.consecutive_long_sl >= cfg.HUNTING2_MAX_CONSECUTIVE_SL_DIR:
                state.long_blocked = True
        else:
            state.consecutive_long_sl = 0
    elif is_sl:
        state.consecutive_short_sl += 1
        if state.consecutive_short_sl >= cfg.HUNTING2_MAX_CONSECUTIVE_SL_DIR:
            state.short_blocked = True
    else:
        state.consecutive_short_sl = 0


def _can_enter(state: DirectionCooldownState, direction: str) -> bool:
    if not cfg.HUNTING2_USE_DIRECTION_COOLDOWN:
        return True
    return not state.long_blocked if direction == "LONG" else not state.short_blocked


def simulate_trades(
    df: pd.DataFrame,
    results: list[BarResult],
) -> tuple[list[SimTrade], list[Signal], list[OpenPosition]]:
    trades: list[SimTrade] = []
    entry_signals: list[Signal] = []
    open_legs: list[OpenPosition] = []
    cd = DirectionCooldownState()

    for i, bar in enumerate(results):
        row = df.iloc[i]
        still_open: list[OpenPosition] = []
        for leg in open_legs:
            closed = _process_bar_exits(leg, row["high"], row["low"], cfg.HUNTING2_TP1_REDUCE_PCT)
            if closed is not None:
                pnl_r, result = closed
                if cfg.HUNTING2_USE_DIRECTION_COOLDOWN:
                    _record_direction_close(cd, leg.direction, result)
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

        if cfg.HUNTING2_USE_DIRECTION_COOLDOWN:
            _unlock_on_opposite_signal(cd, bar)

        if bar.long_sig and (not cfg.HUNTING2_USE_DIRECTION_COOLDOWN or _can_enter(cd, "LONG")):
            _try_open_leg(bar, "long", bar.sl_long, bar.stars_l, open_legs, entry_signals)
        if bar.short_sig and (
            not cfg.HUNTING2_USE_DIRECTION_COOLDOWN or _can_enter(cd, "SHORT")
        ):
            _try_open_leg(bar, "short", bar.sl_short, bar.stars_s, open_legs, entry_signals)

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


def compute_bar_results(df: pd.DataFrame) -> list[BarResult]:
    return Hunting2Engine().compute(df)


def scan_raw_signals(df: pd.DataFrame) -> list[Signal]:
    results = compute_bar_results(df)
    signals: list[Signal] = []
    for bar in results:
        if bar.long_sig:
            plan = build_hunting2_trade_plan("long", bar.close, bar.sl_long)
            if plan:
                signals.append(
                    Signal(
                        bar_index=bar.bar_index,
                        side="long",
                        entry=bar.close,
                        plan=plan,
                        stars=bar.stars_l,
                    )
                )
        if bar.short_sig:
            plan = build_hunting2_trade_plan("short", bar.close, bar.sl_short)
            if plan:
                signals.append(
                    Signal(
                        bar_index=bar.bar_index,
                        side="short",
                        entry=bar.close,
                        plan=plan,
                        stars=bar.stars_s,
                    )
                )
    return signals


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
        tag = "stop_loss" if t.result == "SL" else "final_tp"
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
