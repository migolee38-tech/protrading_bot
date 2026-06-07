"""
Hunting Funding — OI/CVD/量能/趨勢/動能五星評分策略。
供儀表板（scan_signals）與 CLI（hunting_funding.py）共用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

import config as cfg
from core.market_data import fetch_open_interest_history
from risk import TradePlan, build_hunting_trade_plan


@dataclass(frozen=True)
class Signal:
    bar_index: int
    side: str
    entry: float
    plan: TradePlan
    stars: int = 5


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


@dataclass
class OpenPosition:
    direction: str
    entry_time: pd.Timestamp
    entry_price: float
    initial_sl: float
    sl: float
    r1: float
    r3: float
    r5: float
    remaining: float = 1.0
    realized_r: float = 0.0
    stage: int = 0
    events: list[str] = field(default_factory=list)


@dataclass
class SimTrade:
    direction: str
    entry_time: pd.Timestamp
    entry_price: float
    sl: float
    r1: float
    r3: float
    r5: float
    bar_index: int
    exit_price: float = 0.0
    exit_time: Optional[pd.Timestamp] = None
    result: str = ""
    pnl_r: float = 0.0


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


def calc_ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()


def calc_cvd(df: pd.DataFrame) -> pd.Series:
    delta = np.where(df["close"] >= df["open"], df["volume"], -df["volume"])
    return pd.Series(delta, index=df.index).cumsum()


def calc_exit_levels(entry: float, sl: float, direction: str) -> dict[str, float]:
    if direction == "LONG":
        risk = entry - sl
        return {"risk": risk, "sl": sl, "r1": entry + risk, "r3": entry + risk * 3, "r5": entry + risk * 5}
    risk = sl - entry
    return {"risk": risk, "sl": sl, "r1": entry - risk, "r3": entry - risk * 3, "r5": entry - risk * 5}


def prepare_dataframe(raw: pd.DataFrame) -> pd.DataFrame:
    """合併 OI 欄位；需 raw.attrs['symbol']。"""
    out = raw.copy()
    symbol = str(out.attrs.get("symbol", "BTCUSDT")).replace("/", "").upper()
    interval = cfg.HUNTING_FUNDING_TIMEFRAME
    ts = _time_series(out)
    try:
        oi = fetch_open_interest_history(symbol, interval, limit=500)
        if oi.empty:
            out["oi"] = np.nan
        else:
            aligned = oi.reindex(pd.DatetimeIndex(ts), method="ffill")
            out["oi"] = aligned.values
    except Exception:
        out["oi"] = np.nan
    return out


class HuntingEngine:
    def compute(self, df: pd.DataFrame) -> list[BarResult]:
        n = len(df)
        cvd = calc_cvd(df)
        vol_ma = df["volume"].rolling(cfg.HUNTING_VOL_LEN).mean()
        htf_ema = calc_ema(df["close"], cfg.HUNTING_HTF_EMA_LEN)
        ts = _time_series(df)

        if "oi" in df.columns:
            oi_aligned = pd.Series(df["oi"].values, index=df.index)
        else:
            oi_aligned = pd.Series(np.nan, index=df.index)

        win = cfg.HUNTING_SL_SWING + 1
        swing_low = df["low"].rolling(win).min()
        swing_high = df["high"].rolling(win).max()

        results: list[BarResult] = []
        last_bar_l: Optional[int] = None
        last_bar_s: Optional[int] = None

        for i in range(n):
            close_i = float(df["close"].iloc[i])
            htf_i = htf_ema.iloc[i]

            if not np.isnan(htf_i) and htf_i != 0:
                dist_pct = abs(close_i - htf_i) / htf_i * 100
            else:
                dist_pct = np.nan

            bull_htf = not np.isnan(htf_i) and close_i > htf_i
            bear_htf = not np.isnan(htf_i) and close_i < htf_i
            near_ema = not np.isnan(dist_pct) and dist_pct <= cfg.HUNTING_MAX_DIST_PCT

            lb = cfg.HUNTING_LOOKBACK
            oi_i = oi_aligned.iloc[i]
            oi_prev = oi_aligned.iloc[i - lb] if i >= lb else np.nan
            oi_avail = not np.isnan(oi_i) and not np.isnan(oi_prev)
            oi_chg = (oi_i / oi_prev - 1) * 100 if oi_avail else np.nan
            oi_move = oi_avail and abs(oi_chg) >= cfg.HUNTING_OI_MIN_PCT

            cvd_i = cvd.iloc[i]
            cvd_prev = cvd.iloc[i - lb] if i >= lb else np.nan
            vol_i = df["volume"].iloc[i]
            volma_i = vol_ma.iloc[i]
            close_mom = df["close"].iloc[i - cfg.HUNTING_MOM_LEN] if i >= cfg.HUNTING_MOM_LEN else np.nan

            f_oi = cfg.HUNTING_USE_OI and oi_move
            f_vol = cfg.HUNTING_USE_VOL and not np.isnan(volma_i) and vol_i > volma_i
            f_cvd_l = cfg.HUNTING_USE_CVD and not np.isnan(cvd_prev) and cvd_i > cvd_prev
            f_cvd_s = cfg.HUNTING_USE_CVD and not np.isnan(cvd_prev) and cvd_i < cvd_prev
            f_trend_l = cfg.HUNTING_USE_TREND and not np.isnan(htf_i) and close_i > htf_i
            f_trend_s = cfg.HUNTING_USE_TREND and not np.isnan(htf_i) and close_i < htf_i
            f_mom_l = cfg.HUNTING_USE_MOM and not np.isnan(close_mom) and close_i > close_mom
            f_mom_s = cfg.HUNTING_USE_MOM and not np.isnan(close_mom) and close_i < close_mom

            max_score = (
                (cfg.HUNTING_W_OI if cfg.HUNTING_USE_OI and oi_avail else 0)
                + (cfg.HUNTING_W_CVD if cfg.HUNTING_USE_CVD else 0)
                + (cfg.HUNTING_W_VOL if cfg.HUNTING_USE_VOL else 0)
                + (cfg.HUNTING_W_TREND if cfg.HUNTING_USE_TREND else 0)
                + (cfg.HUNTING_W_MOM if cfg.HUNTING_USE_MOM else 0)
            )
            raw_l = (
                (cfg.HUNTING_W_OI if f_oi else 0)
                + (cfg.HUNTING_W_CVD if f_cvd_l else 0)
                + (cfg.HUNTING_W_VOL if f_vol else 0)
                + (cfg.HUNTING_W_TREND if f_trend_l else 0)
                + (cfg.HUNTING_W_MOM if f_mom_l else 0)
            )
            raw_s = (
                (cfg.HUNTING_W_OI if f_oi else 0)
                + (cfg.HUNTING_W_CVD if f_cvd_s else 0)
                + (cfg.HUNTING_W_VOL if f_vol else 0)
                + (cfg.HUNTING_W_TREND if f_trend_s else 0)
                + (cfg.HUNTING_W_MOM if f_mom_s else 0)
            )

            stars_l = int(round(raw_l / max_score * 5)) if max_score > 0 else 0
            stars_s = int(round(raw_s / max_score * 5)) if max_score > 0 else 0

            prev_stars_l = results[-1].stars_l if results else 0
            prev_stars_s = results[-1].stars_s if results else 0
            cross_l = stars_l >= cfg.HUNTING_MIN_STARS and prev_stars_l < cfg.HUNTING_MIN_STARS
            cross_s = stars_s >= cfg.HUNTING_MIN_STARS and prev_stars_s < cfg.HUNTING_MIN_STARS

            can_fire_l = last_bar_l is None or (i - last_bar_l) >= cfg.HUNTING_COOLDOWN_BARS
            can_fire_s = last_bar_s is None or (i - last_bar_s) >= cfg.HUNTING_COOLDOWN_BARS

            sl_long = swing_low.iloc[i]
            sl_short = swing_high.iloc[i]
            sl_pct_l = (close_i - sl_long) / close_i * 100 if close_i and not np.isnan(sl_long) else np.nan
            sl_pct_s = (sl_short - close_i) / close_i * 100 if close_i and not np.isnan(sl_short) else np.nan
            sl_ok_l = not np.isnan(sl_pct_l) and sl_pct_l <= cfg.HUNTING_MAX_SL_PCT
            sl_ok_s = not np.isnan(sl_pct_s) and sl_pct_s <= cfg.HUNTING_MAX_SL_PCT

            long_sig = cross_l and bull_htf and near_ema and sl_ok_l and can_fire_l
            short_sig = cross_s and bear_htf and near_ema and sl_ok_s and can_fire_s

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
                )
            )
        return results


def _sl_hit(pos: OpenPosition, low: float, high: float) -> bool:
    if pos.direction == "LONG":
        return low <= pos.sl
    return high >= pos.sl


def _tp_hit(pos: OpenPosition, level: float, low: float, high: float) -> bool:
    if pos.direction == "LONG":
        return high >= level
    return low <= level


def _process_bar_exits(
    pos: OpenPosition, high: float, low: float, tp1_reduce: float,
) -> Optional[tuple[float, str]]:
    while pos.remaining > 1e-9:
        acted = False
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
                acted = True
                continue
        if pos.stage == 1:
            if _tp_hit(pos, pos.r3, low, high):
                pos.sl = pos.r1
                pos.stage = 2
                pos.events.append("3R")
                acted = True
                continue
            if _sl_hit(pos, low, high):
                pos.remaining = 0.0
                return pos.realized_r, "+".join(pos.events + ["BE"])
        if pos.stage == 2:
            if _tp_hit(pos, pos.r5, low, high):
                pos.realized_r += pos.remaining * 5.0
                pos.remaining = 0.0
                pos.events.append("5R")
                return pos.realized_r, "+".join(pos.events)
            if _sl_hit(pos, low, high):
                pos.realized_r += pos.remaining * 1.0
                pos.remaining = 0.0
                return pos.realized_r, "+".join(pos.events + ["SL@1R"])
        if not acted:
            break
    return None


def _open_position(direction: str, entry_time: pd.Timestamp, entry: float, sl: float, bar_index: int) -> OpenPosition:
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
    )


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
            if state.consecutive_long_sl >= cfg.HUNTING_MAX_CONSECUTIVE_SL_DIR:
                state.long_blocked = True
        else:
            state.consecutive_long_sl = 0
    elif is_sl:
        state.consecutive_short_sl += 1
        if state.consecutive_short_sl >= cfg.HUNTING_MAX_CONSECUTIVE_SL_DIR:
            state.short_blocked = True
    else:
        state.consecutive_short_sl = 0


def _can_enter(state: DirectionCooldownState, direction: str) -> bool:
    if not cfg.HUNTING_USE_DIRECTION_COOLDOWN:
        return True
    return not state.long_blocked if direction == "LONG" else not state.short_blocked


def simulate_trades(
    df: pd.DataFrame, results: list[BarResult],
) -> tuple[list[SimTrade], list[Signal]]:
    trades: list[SimTrade] = []
    entry_signals: list[Signal] = []
    open_pos: Optional[OpenPosition] = None
    cd = DirectionCooldownState()
    entry_bar_index = 0

    for i, bar in enumerate(results):
        row = df.iloc[i]
        if open_pos is not None:
            closed = _process_bar_exits(open_pos, row["high"], row["low"], cfg.HUNTING_TP1_REDUCE_PCT)
            if closed is not None:
                pnl_r, result = closed
                if cfg.HUNTING_USE_DIRECTION_COOLDOWN:
                    _record_direction_close(cd, open_pos.direction, result)
                trades.append(
                    SimTrade(
                        direction=open_pos.direction,
                        entry_time=open_pos.entry_time,
                        entry_price=open_pos.entry_price,
                        sl=open_pos.initial_sl,
                        r1=open_pos.r1,
                        r3=open_pos.r3,
                        r5=open_pos.r5,
                        bar_index=entry_bar_index,
                        exit_time=bar.ts,
                        exit_price=float(row["close"]),
                        result=result,
                        pnl_r=pnl_r,
                    )
                )
                open_pos = None

        if cfg.HUNTING_USE_DIRECTION_COOLDOWN:
            _unlock_on_opposite_signal(cd, bar)

        eff_long = bar.long_sig and _can_enter(cd, "LONG")
        eff_short = bar.short_sig and _can_enter(cd, "SHORT")

        if open_pos is None:
            if eff_long:
                sl = bar.sl_long
                side = "long"
                plan = build_hunting_trade_plan(side, bar.close, sl)
                if plan:
                    entry_signals.append(
                        Signal(bar_index=bar.bar_index, side=side, entry=bar.close, plan=plan, stars=bar.stars_l)
                    )
                    open_pos = _open_position("LONG", bar.ts, bar.close, sl, bar.bar_index)
                    entry_bar_index = bar.bar_index
            elif eff_short:
                sl = bar.sl_short
                side = "short"
                plan = build_hunting_trade_plan(side, bar.close, sl)
                if plan:
                    entry_signals.append(
                        Signal(bar_index=bar.bar_index, side=side, entry=bar.close, plan=plan, stars=bar.stars_s)
                    )
                    open_pos = _open_position("SHORT", bar.ts, bar.close, sl, bar.bar_index)
                    entry_bar_index = bar.bar_index

    if open_pos is not None:
        trades.append(
            SimTrade(
                direction=open_pos.direction,
                entry_time=open_pos.entry_time,
                entry_price=open_pos.entry_price,
                sl=open_pos.initial_sl,
                r1=open_pos.r1,
                r3=open_pos.r3,
                r5=open_pos.r5,
                bar_index=entry_bar_index,
                result="OPEN",
                pnl_r=open_pos.realized_r,
            )
        )
    return trades, entry_signals


def compute_bar_results(df: pd.DataFrame) -> list[BarResult]:
    return HuntingEngine().compute(df)


def scan_signals(df: pd.DataFrame) -> list[Signal]:
    results = compute_bar_results(df)
    _, entry_signals = simulate_trades(df, results)
    return entry_signals


def run_dashboard_backtest(df: pd.DataFrame) -> dict:
    """供 backtest_report 使用的統計摘要。"""
    results = compute_bar_results(df)
    raw_signals = sum(1 for r in results if r.long_sig or r.short_sig)
    trades, _ = simulate_trades(df, results)
    closed = [t for t in trades if t.result != "OPEN"]
    wins = sum(1 for t in closed if t.pnl_r > 0)
    losses = sum(1 for t in closed if t.pnl_r < 0)
    closed_n = wins + losses
    win_rate = (wins / closed_n) if closed_n else 0.0
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
    }
