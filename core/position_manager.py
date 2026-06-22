"""實盤持倉管理：分批止盈、移動止損（EMA / 唐奇安 / Hunting Funding）。"""

from __future__ import annotations

import logging
from typing import Any

import config as cfg
from core.account_profiles import AccountProfile
from core.futures_execution import (
    cancel_algo_order,
    create_futures_clients,
    exchange_position_qty,
    format_futures_error,
    get_mark_price,
    place_market_reduce,
    place_tp_algo,
    replace_stop_algo,
    settings_for_profile,
)
from core.live_positions import (
    LivePositionState,
    list_open_positions,
    new_position_id,
    upsert_position,
)
from core.market_data import fetch_klines
from core.strategy_registry import get_strategy
from position import Position
from position_donchian import DonchianPosition
from risk import TradePlan, recalc_plan_for_fill
from strategies.hunting_funding import (
    OpenPosition as HuntingLeg,
    _process_bar_exits,
)

log = logging.getLogger(__name__)


def _fill_price(entry_result: dict, fallback: float) -> float:
    for key in ("avgPrice", "avgPx", "price", "activatePrice", "fillPx"):
        val = entry_result.get(key)
        if val is not None and float(val) > 0:
            return float(val)
    return fallback


def _place_tp_algo(
    clients: Any,
    state: LivePositionState,
    trigger_price: float,
    quantity: float,
) -> None:
    algo_id = place_tp_algo(
        clients,
        symbol=state.symbol,
        side=state.side,
        trigger_price=trigger_price,
        quantity=quantity,
    )
    if algo_id:
        state.tp_algo_ids.append(algo_id)


def _replace_stop_algo(clients: Any, state: LivePositionState, new_stop: float) -> None:
    if state.remaining_qty <= 0 or state.closed:
        return
    state.stop = new_stop
    state.stop_algo_id = replace_stop_algo(
        clients,
        symbol=state.symbol,
        side=state.side,
        new_stop=new_stop,
        quantity=state.remaining_qty,
        stop_algo_id=state.stop_algo_id,
    )


def _cancel_tp_algos(clients: Any, state: LivePositionState) -> None:
    for algo_id in list(state.tp_algo_ids):
        cancel_algo_order(clients, algo_id, symbol=state.symbol)
    state.tp_algo_ids = []


def _cancel_all_algos(clients: Any, state: LivePositionState) -> None:
    if state.stop_algo_id:
        cancel_algo_order(clients, state.stop_algo_id, symbol=state.symbol)
        state.stop_algo_id = None
    _cancel_tp_algos(clients, state)


def place_entry_protection(clients: Any, state: LivePositionState) -> None:
    """依策略掛初始止損與第一批止盈。"""
    _replace_stop_algo(clients, state, state.stop)

    if state.strategy_id == "ema":
        qty = state.initial_qty * cfg.REDUCE_AT_1R_PCT
        _place_tp_algo(clients, state, state.tp_1r, qty)
    elif state.strategy_id == "donchian":
        qty = state.initial_qty * cfg.DONCHIAN_REDUCE_TP1_PCT
        _place_tp_algo(clients, state, state.tp_1r, qty)
    elif state.strategy_id == "hunting_funding":
        tp1 = state.initial_qty * cfg.HUNTING_TP1_REDUCE_PCT
        rest = max(0.0, state.remaining_qty - tp1)
        _place_tp_algo(clients, state, state.tp_1r, tp1)
        if rest > 0:
            _place_tp_algo(clients, state, state.tp_final, rest)
    elif state.strategy_id == "smc_ict":
        tp1 = state.initial_qty * cfg.SMC_TP1_REDUCE_PCT
        rest = max(0.0, state.remaining_qty - tp1)
        _place_tp_algo(clients, state, state.tp_1r, tp1)
        if rest > 0:
            _place_tp_algo(clients, state, state.tp_final, rest)


def register_live_position(
    profile: AccountProfile,
    clients: Any,
    *,
    strategy_id: str,
    symbol: str,
    side: str,
    plan: TradePlan,
    quantity: float,
    entry_result: dict,
    exchange_order_id: str = "",
) -> LivePositionState:
    """市價進場後登記持倉並掛保護單。"""
    fill = _fill_price(entry_result, plan.entry)
    adj = recalc_plan_for_fill(plan, fill, strategy_id)
    sym = symbol.replace("/", "").upper()
    qty = float(quantity)
    state = LivePositionState(
        position_id=new_position_id(strategy_id, sym),
        symbol=sym,
        strategy_id=strategy_id,
        side=side,
        entry_price=adj.entry,
        initial_qty=qty,
        remaining_qty=qty,
        stop=adj.stop,
        plan_entry=adj.entry,
        plan_stop=adj.stop,
        plan_r=adj.r,
        tp_1r=adj.tp_1r,
        tp_2r=adj.tp_2r,
        tp_final=adj.tp_final,
        account_id=profile.account_id,
        exchange_order_id=str(
            exchange_order_id
            or entry_result.get("orderId")
            or entry_result.get("ordId")
            or ""
        ),
    )
    try:
        place_entry_protection(clients, state)
    except Exception as e:
        log.error(f"{sym} 保護單掛載失敗: {format_futures_error(e)}")
    upsert_position(profile, state)
    log.info(
        f"登記持倉 {strategy_id} {sym} {side} entry={adj.entry:.6g} "
        f"sl={adj.stop:.6g} qty={qty}"
    )
    return state


def _bar_high_low(symbol: str, strategy_id: str, mark: float) -> tuple[float, float]:
    """取策略週期最新 K 線高低；失敗時以 mark 代替。"""
    try:
        meta = get_strategy(strategy_id)
        raw = fetch_klines(symbol, interval=meta.timeframe, limit=3, market="futures")
        if raw is not None and len(raw) >= 1:
            row = raw.iloc[-1]
            high = float(row["high"])
            low = float(row["low"])
            return max(high, mark), min(low, mark)
    except Exception as e:
        log.debug(f"{symbol} K線讀取失敗: {e}")
    return mark, mark


def _build_ema_position(state: LivePositionState) -> Position:
    plan = state.to_trade_plan()
    pos = Position(
        symbol=state.symbol,
        plan=plan,
        initial_size=state.initial_qty,
        size=state.remaining_qty,
    )
    pos.stop = state.stop
    pos.reduced_1r = state.reduced_1r
    pos.reduced_2r = state.reduced_2r
    pos.trailing_active = state.trailing_active
    pos.peak_r = state.peak_r
    pos.closed = state.closed
    return pos


def _build_donchian_position(state: LivePositionState) -> DonchianPosition:
    plan = state.to_trade_plan()
    pos = DonchianPosition(
        symbol=state.symbol,
        plan=plan,
        initial_size=state.initial_qty,
        size=state.remaining_qty,
    )
    pos.stop = state.stop
    pos.reduced_tp1 = state.reduced_1r
    pos.reduced_tp2 = state.reduced_2r
    pos.trailing_active = state.trailing_active
    pos.peak_r = state.peak_r
    pos.closed = state.closed
    return pos


def _sync_position_state(state: LivePositionState, pos: Position | DonchianPosition) -> None:
    state.stop = pos.stop
    state.remaining_qty = pos.size
    if isinstance(pos, DonchianPosition):
        state.reduced_1r = pos.reduced_tp1
        state.reduced_2r = pos.reduced_tp2
    else:
        state.reduced_1r = pos.reduced_1r
        state.reduced_2r = pos.reduced_2r
    state.trailing_active = pos.trailing_active
    state.peak_r = pos.peak_r
    state.closed = pos.closed


def _market_reduce_qty(
    clients: Any,
    state: LivePositionState,
    qty: float,
) -> float:
    before = exchange_position_qty(clients, state.symbol, state.side)
    if before <= 0:
        state.remaining_qty = 0.0
        state.closed = True
        return 0.0
    cut = min(qty, before, state.remaining_qty)
    if cut <= 0:
        state.remaining_qty = before
        return 0.0
    try:
        place_market_reduce(clients, symbol=state.symbol, side=state.side, quantity=cut)
    except Exception as e:
        log.error(f"{state.symbol} 減倉失敗: {format_futures_error(e)}")
        return 0.0
    after = exchange_position_qty(clients, state.symbol, state.side)
    reduced = max(0.0, before - after)
    state.remaining_qty = after
    if state.remaining_qty <= 0 or after <= 0:
        state.remaining_qty = 0.0
        state.closed = True
    return reduced


def _qty_already_at_target(
    clients: Any,
    state: LivePositionState,
    target_qty: float,
    *,
    tolerance_ratio: float = 0.02,
) -> bool:
    ex_qty = exchange_position_qty(clients, state.symbol, state.side)
    if ex_qty <= 0:
        return False
    tol = max(state.initial_qty * tolerance_ratio, 1e-8)
    return ex_qty <= target_qty + tol


def _apply_target_qty(clients: Any, state: LivePositionState, target_qty: float) -> None:
    ex_qty = exchange_position_qty(clients, state.symbol, state.side)
    if ex_qty > target_qty:
        _market_reduce_qty(clients, state, ex_qty - target_qty)
    else:
        state.remaining_qty = ex_qty


def _after_partial_tp_ema(clients: Any, state: LivePositionState, event: str) -> None:
    _cancel_tp_algos(clients, state)
    if event == "partial_tp_1r":
        _replace_stop_algo(clients, state, state.entry_price)
        qty = state.initial_qty * cfg.REDUCE_AT_2R_PCT
        _place_tp_algo(clients, state, state.tp_2r, qty)
    elif event == "partial_tp_2r":
        plan = state.to_trade_plan()
        _replace_stop_algo(clients, state, plan.stop_1r)
        if state.remaining_qty > 0:
            _place_tp_algo(clients, state, state.tp_final, state.remaining_qty)


def _after_partial_tp_donchian(clients: Any, state: LivePositionState, event: str) -> None:
    _cancel_tp_algos(clients, state)
    if event.startswith("partial_tp_2r"):
        _replace_stop_algo(clients, state, state.entry_price)
    elif event.startswith("partial_tp_5r"):
        plan = state.to_trade_plan()
        _replace_stop_algo(clients, state, plan.stop_3r)
        if state.remaining_qty > 0:
            _place_tp_algo(clients, state, state.tp_final, state.remaining_qty)


def _finalize_position(clients: Any, state: LivePositionState) -> None:
    qty = exchange_position_qty(clients, state.symbol, state.side)
    if qty > 0:
        try:
            place_market_reduce(clients, symbol=state.symbol, side=state.side, quantity=qty)
        except Exception as e:
            log.error(f"{state.symbol} 平倉失敗: {format_futures_error(e)}")
    _cancel_all_algos(clients, state)
    state.remaining_qty = 0.0
    state.closed = True


def _execute_ema_events(clients: Any, state: LivePositionState, events: list[str]) -> None:
    for ev in events:
        if ev.startswith("partial_tp_1r"):
            target = state.initial_qty * (1.0 - cfg.REDUCE_AT_1R_PCT)
            if not _qty_already_at_target(clients, state, target):
                _apply_target_qty(clients, state, target)
            _after_partial_tp_ema(clients, state, "partial_tp_1r")
        elif ev.startswith("partial_tp_2r"):
            target = state.initial_qty * (1.0 - cfg.REDUCE_AT_1R_PCT - cfg.REDUCE_AT_2R_PCT)
            if not _qty_already_at_target(clients, state, target):
                _apply_target_qty(clients, state, target)
            _after_partial_tp_ema(clients, state, "partial_tp_2r")
        elif ev == "hedge_to_entry":
            _replace_stop_algo(clients, state, state.entry_price)
        elif ev == "hedge_to_1r":
            plan = state.to_trade_plan()
            _replace_stop_algo(clients, state, plan.stop_1r)
        elif ev in ("stop_loss", "final_tp_10r"):
            _finalize_position(clients, state)


def _execute_donchian_events(clients: Any, state: LivePositionState, events: list[str]) -> None:
    for ev in events:
        if ev.startswith("partial_tp_2r"):
            target = state.initial_qty * (1.0 - cfg.DONCHIAN_REDUCE_TP1_PCT)
            if not _qty_already_at_target(clients, state, target):
                _apply_target_qty(clients, state, target)
            _after_partial_tp_donchian(clients, state, ev)
        elif ev.startswith("partial_tp_5r"):
            target = (
                state.initial_qty
                * (1.0 - cfg.DONCHIAN_REDUCE_TP1_PCT)
                * (1.0 - cfg.DONCHIAN_REDUCE_TP2_PCT)
            )
            if not _qty_already_at_target(clients, state, target):
                _apply_target_qty(clients, state, target)
            _after_partial_tp_donchian(clients, state, ev)
        elif ev == "hedge_to_entry":
            _replace_stop_algo(clients, state, state.entry_price)
        elif ev == "hedge_to_3r":
            plan = state.to_trade_plan()
            _replace_stop_algo(clients, state, plan.stop_3r)
        elif ev in ("stop_loss", "final_tp_10r"):
            _finalize_position(clients, state)


def _tick_hunting_like(
    clients: Any,
    state: LivePositionState,
    high: float,
    low: float,
    *,
    tp1_reduce: float,
    log_tag: str,
) -> None:
    direction = "LONG" if state.side == "long" else "SHORT"
    leg = HuntingLeg(
        direction=direction,
        entry_time=state.created_at,  # type: ignore[arg-type]
        entry_price=state.entry_price,
        initial_sl=state.plan_stop,
        sl=state.stop,
        r1=state.tp_1r,
        r3=state.tp_2r,
        r5=state.tp_final,
        remaining=state.remaining_qty / state.initial_qty if state.initial_qty > 0 else 0.0,
        stage=state.stage,
    )
    before_stage = leg.stage
    before_sl = leg.sl

    closed = _process_bar_exits(leg, high, low, tp1_reduce)

    state.stage = leg.stage
    state.stop = leg.sl
    state.remaining_qty = leg.remaining * state.initial_qty

    if leg.stage == 1 and before_stage == 0:
        target = state.initial_qty * (1.0 - tp1_reduce)
        if not _qty_already_at_target(clients, state, target):
            _apply_target_qty(clients, state, target)
        _cancel_tp_algos(clients, state)
        _replace_stop_algo(clients, state, state.entry_price)
        rest = state.remaining_qty
        if rest > 0:
            _place_tp_algo(clients, state, state.tp_final, rest)

    if leg.stage == 2 and before_stage == 1:
        _replace_stop_algo(clients, state, leg.r1)

    if leg.sl != before_sl and leg.stage >= 1 and before_stage == leg.stage:
        _replace_stop_algo(clients, state, leg.sl)

    if closed is not None:
        log.info(f"[{log_tag}] {state.symbol} 平倉 {closed[1]} pnl_r={closed[0]:.2f}")
        _finalize_position(clients, state)


def _tick_hunting(clients: Any, state: LivePositionState, high: float, low: float) -> None:
    _tick_hunting_like(
        clients,
        state,
        high,
        low,
        tp1_reduce=cfg.HUNTING_TP1_REDUCE_PCT,
        log_tag="hunting_funding",
    )


def _tick_smc(clients: Any, state: LivePositionState, high: float, low: float) -> None:
    _tick_hunting_like(
        clients,
        state,
        high,
        low,
        tp1_reduce=cfg.SMC_TP1_REDUCE_PCT,
        log_tag="smc_ict",
    )


def _tick_ema_or_donchian(
    clients: Any,
    state: LivePositionState,
    high: float,
    low: float,
) -> None:
    stop_before = state.stop
    if state.strategy_id == "donchian":
        pos = _build_donchian_position(state)
        events = pos.on_bar(high, low)
        _sync_position_state(state, pos)
        _execute_donchian_events(clients, state, events)
    else:
        pos = _build_ema_position(state)
        events = pos.on_bar(high, low)
        _sync_position_state(state, pos)
        _execute_ema_events(clients, state, events)

    if (
        state.trailing_active
        and not state.closed
        and state.stop != stop_before
        and not any(e.startswith("partial_tp") or e.startswith("hedge") for e in events)
    ):
        _replace_stop_algo(clients, state, state.stop)


def _sync_exchange_qty(clients: Any, state: LivePositionState) -> None:
    qty = exchange_position_qty(clients, state.symbol, state.side)
    if qty <= 0:
        if not state.closed:
            log.info(f"{state.symbol} 交易所無持倉，清除管理狀態")
            _cancel_all_algos(clients, state)
            state.remaining_qty = 0.0
            state.closed = True
        return
    if abs(qty - state.remaining_qty) > 1e-8:
        state.remaining_qty = qty


def _ensure_protection(clients: Any, state: LivePositionState) -> None:
    if state.closed or state.remaining_qty <= 0:
        return
    if not state.stop_algo_id:
        log.warning(f"{state.symbol} 缺少止損單，嘗試補掛")
        _replace_stop_algo(clients, state, state.stop)


def tick_position(
    clients: Any,
    state: LivePositionState,
    *,
    high: float,
    low: float,
) -> LivePositionState:
    if state.closed:
        return state
    _sync_exchange_qty(clients, state)
    if state.closed:
        return state
    _ensure_protection(clients, state)

    if state.strategy_id == "hunting_funding":
        _tick_hunting(clients, state, high, low)
    elif state.strategy_id == "smc_ict":
        _tick_smc(clients, state, high, low)
    else:
        _tick_ema_or_donchian(clients, state, high, low)

    return state


def manage_positions_for_profile(
    profile: AccountProfile,
    settings: Any | None = None,
) -> int:
    """輪詢並管理該 profile 所有開倉；回傳更新筆數。"""
    if profile.network.value == "paper":
        return 0

    open_positions = list_open_positions(profile)
    if not open_positions:
        return 0

    fs = settings or settings_for_profile(profile)
    clients = create_futures_clients(fs)
    updated = 0

    for state in open_positions:
        try:
            mark = get_mark_price(clients, state.symbol)
            high, low = _bar_high_low(state.symbol, state.strategy_id, mark)
            tick_position(clients, state, high=high, low=low)
            upsert_position(profile, state)
            updated += 1
        except Exception as e:
            log.error(
                f"[{profile.display_name}] 持倉管理失敗 {state.symbol} "
                f"{state.strategy_id}: {format_futures_error(e)}"
            )

    return updated
