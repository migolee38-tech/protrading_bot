"""
SMC / ICT — 15m BOS + Liquidity Sweep + Order Block 回踩進場。
供儀表板、回測與 live_runner 共用。
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

import config as cfg
from core.smc_structure import (
    build_structure_states,
    long_setup_valid,
    price_touches_ob,
    short_setup_valid,
)
from risk import TradePlan, build_smc_trade_plan
from strategies.hunting_funding import (
    OpenPosition,
    SimTrade,
    _process_bar_exits,
    calc_exit_levels,
)


@dataclass(frozen=True)
class Signal:
    bar_index: int
    side: str
    entry: float
    plan: TradePlan
    setup: str = "smc"


@dataclass
class BarResult:
    bar_index: int
    ts: pd.Timestamp
    close: float
    long_sig: bool
    short_sig: bool
    ob_low: float = 0.0
    ob_high: float = 0.0
    sweep_level: float = 0.0
    sl_long: float = 0.0
    sl_short: float = 0.0


def _time_series(df: pd.DataFrame) -> pd.Series:
    if "datetime" in df.columns:
        return pd.to_datetime(df["datetime"], utc=True)
    return pd.to_datetime(df.index, utc=True)


def prepare_dataframe(raw: pd.DataFrame) -> pd.DataFrame:
    return raw.copy()


def compute_bar_results(df: pd.DataFrame) -> list[BarResult]:
    states = build_structure_states(df)
    ts = _time_series(df)
    results: list[BarResult] = []
    last_long = last_short = -10_000

    for i in range(len(df)):
        row = df.iloc[i]
        st = states[i]
        long_sig = short_sig = False
        ob_lo = ob_hi = sweep_lv = 0.0
        sl_long = sl_short = 0.0

        setup_l = long_setup_valid(st, i)
        if setup_l and i - last_long >= cfg.SMC_COOLDOWN_BARS:
            ob, sweep_lv = setup_l
            if price_touches_ob(row, ob):
                plan = build_smc_trade_plan("long", float(row["close"]), ob.low, ob.high, sweep_lv)
                if plan:
                    long_sig = True
                    ob_lo, ob_hi = ob.low, ob.high
                    sl_long = plan.stop
                    last_long = i

        setup_s = short_setup_valid(st, i)
        if setup_s and i - last_short >= cfg.SMC_COOLDOWN_BARS:
            ob, sweep_lv = setup_s
            if price_touches_ob(row, ob):
                plan = build_smc_trade_plan("short", float(row["close"]), ob.low, ob.high, sweep_lv)
                if plan:
                    short_sig = True
                    ob_lo, ob_hi = ob.low, ob.high
                    sl_short = plan.stop
                    last_short = i

        results.append(
            BarResult(
                bar_index=i,
                ts=ts.iloc[i],
                close=float(row["close"]),
                long_sig=long_sig,
                short_sig=short_sig,
                ob_low=ob_lo,
                ob_high=ob_hi,
                sweep_level=sweep_lv,
                sl_long=sl_long,
                sl_short=sl_short,
            )
        )
    return results


def scan_raw_signals(df: pd.DataFrame) -> list[Signal]:
    """圖表 markers：所有 OB 回踩觸發（不含模擬倉位上限）。"""
    signals: list[Signal] = []
    for bar in compute_bar_results(df):
        if bar.long_sig and bar.sl_long > 0:
            plan = build_smc_trade_plan(
                "long", bar.close, bar.ob_low, bar.ob_high, bar.sweep_level
            )
            if plan:
                signals.append(
                    Signal(bar_index=bar.bar_index, side="long", entry=bar.close, plan=plan)
                )
        if bar.short_sig and bar.sl_short > 0:
            plan = build_smc_trade_plan(
                "short", bar.close, bar.ob_low, bar.ob_high, bar.sweep_level
            )
            if plan:
                signals.append(
                    Signal(bar_index=bar.bar_index, side="short", entry=bar.close, plan=plan)
                )
    return signals


def _open_leg(direction: str, entry_time: pd.Timestamp, entry: float, sl: float, bar_index: int) -> OpenPosition:
    lv = calc_exit_levels(entry, sl, direction)
    return OpenPosition(
        direction=direction,
        entry_time=entry_time,
        entry_price=entry,
        initial_sl=sl,
        sl=lv["sl"],
        r1=lv["r1"],
        r3=lv["r3"],
        r5=lv["r5"],
        entry_bar_index=bar_index,
    )


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
            closed = _process_bar_exits(leg, row["high"], row["low"], cfg.SMC_TP1_REDUCE_PCT)
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

        if bar.long_sig and bar.sl_long > 0:
            plan = build_smc_trade_plan(
                "long", bar.close, bar.ob_low, bar.ob_high, bar.sweep_level
            )
            if plan:
                entry_signals.append(
                    Signal(bar_index=bar.bar_index, side="long", entry=bar.close, plan=plan)
                )
                open_legs.append(_open_leg("LONG", bar.ts, bar.close, bar.sl_long, bar.bar_index))

        if bar.short_sig and bar.sl_short > 0:
            plan = build_smc_trade_plan(
                "short", bar.close, bar.ob_low, bar.ob_high, bar.sweep_level
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
    """live_runner / 回測用：含模擬持倉出場邏輯後的實際進場訊號。"""
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
