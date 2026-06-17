"""永續下單／持倉管理：依 EXCHANGE 分派 Binance / OKX。"""

from __future__ import annotations

import logging
import time
from typing import Any

from core.account_profiles import AccountProfile
from core.exchange_config import is_okx
from core.exchange_bridge import futures_settings_from_profile

log = logging.getLogger(__name__)


def create_futures_clients(settings: Any) -> Any:
    if is_okx():
        from core.okx_futures import create_clients

        return create_clients(settings)
    from core.binance_futures import create_client

    return create_client(settings)


def format_futures_error(exc: Exception) -> str:
    if is_okx():
        from core.okx_futures import format_okx_error

        return format_okx_error(exc)
    from core.binance_futures import format_binance_error

    return format_binance_error(exc)


def settings_for_profile(
    profile: AccountProfile,
    *,
    leverage: int = 10,
    total_capital: float | None = None,
    position_pct: float | None = None,
) -> Any:
    return futures_settings_from_profile(
        profile,
        leverage=leverage,
        total_capital=total_capital,
        position_pct=position_pct,
    )


def resolve_leverage(clients: Any, symbol: str, settings: Any) -> int:
    sym = symbol.replace("/", "").upper()
    if is_okx():
        lev = int(getattr(settings, "leverage", 0) or 0)
        if lev > 0:
            from core.okx_futures import set_leverage

            set_leverage(clients.account, sym, lev)
            return lev
        return 10
    from core.binance_futures import resolve_leverage as binance_resolve

    return binance_resolve(clients, sym, settings)


def calc_order_quantity(
    *,
    clients: Any,
    symbol: str,
    entry: float,
    position_size: float,
    strategy_id: str,
    settings: Any,
    leverage: int,
) -> float:
    if strategy_id == "donchian" and position_size > 0:
        qty = float(position_size)
    elif entry <= 0:
        return 0.0
    else:
        margin = settings.margin_per_trade
        qty = margin * leverage / entry

    sym = symbol.replace("/", "").upper()
    if is_okx():
        from core.okx_futures import (
            coin_qty_to_contracts,
            contracts_to_coin,
            get_instrument,
        )

        instrument = get_instrument(clients.public, sym)
        contracts = coin_qty_to_contracts(qty, instrument)
        return contracts_to_coin(contracts, instrument)

    from core.binance_futures import _round_qty, _symbol_filters

    lot_step, _ = _symbol_filters(clients, sym)
    return _round_qty(qty, lot_step)


def ensure_symbol_tradable(clients: Any, symbol: str, *, testnet: bool) -> None:
    sym = symbol.replace("/", "").upper()
    if is_okx():
        from core.okx_futures import get_instrument, to_inst_id

        row = get_instrument(clients.public, sym)
        if str(row.get("state", "")).lower() != "live":
            net = "Demo" if testnet else "主網"
            raise ValueError(f"{to_inst_id(sym)} 在 {net} 不可交易（state={row.get('state')}）")
        return
    from core.binance_futures import ensure_tradable_symbol

    ensure_tradable_symbol(clients, sym, testnet=testnet)


def get_tradable_symbols(clients: Any, *, testnet: bool) -> frozenset[str]:
    if is_okx():
        from core.okx_futures import from_inst_id

        response = clients.public.get_instruments("SWAP")
        code = str(response.get("code", ""))
        if code != "0":
            return frozenset()
        symbols = {
            from_inst_id(str(row.get("instId", "")))
            for row in response.get("data") or []
            if str(row.get("state", "")).lower() == "live"
            and str(row.get("instId", "")).endswith("-USDT-SWAP")
        }
        return frozenset(symbols)
    from core.binance_futures import get_tradable_symbols as binance_symbols

    return binance_symbols(clients, testnet=testnet)


def fetch_entry_fill_price(clients: Any, symbol: str, entry_result: dict) -> float:
    if not is_okx():
        for key in ("avgPrice", "price", "activatePrice"):
            val = entry_result.get(key)
            if val is not None and float(val) > 0:
                return float(val)
        return 0.0

    from core.okx_futures import ensure_ok_response, to_inst_id

    inst_id = to_inst_id(symbol)
    ord_id = str(entry_result.get("ordId") or entry_result.get("orderId") or "")
    if not ord_id:
        return 0.0
    for _ in range(3):
        response = clients.trade.get_order(instId=inst_id, ordId=ord_id)
        try:
            ensure_ok_response(response, context="查詢成交均價")
        except Exception:
            time.sleep(0.4)
            continue
        rows = response.get("data") or []
        if rows:
            avg = float(rows[0].get("avgPx") or 0)
            if avg > 0:
                return avg
        time.sleep(0.4)
    return 0.0


def place_market_entry(
    clients: Any,
    *,
    symbol: str,
    side: str,
    quantity: float,
    client_order_id: str = "",
) -> dict[str, Any]:
    if is_okx():
        from core.okx_futures import place_market_entry as okx_entry

        return okx_entry(
            clients,
            symbol=symbol,
            side=side,
            quantity=quantity,
            cl_ord_id=client_order_id,
        )
    sym = symbol.replace("/", "").upper()
    order_side = "BUY" if side == "long" else "SELL"
    kwargs: dict[str, Any] = {
        "symbol": sym,
        "side": order_side,
        "type": "MARKET",
        "quantity": quantity,
    }
    if client_order_id:
        kwargs["newClientOrderId"] = client_order_id
    return clients.new_order(**kwargs)


def get_mark_price(clients: Any, symbol: str) -> float:
    if is_okx():
        from core.okx_futures import get_mark_price as okx_mark

        return okx_mark(clients.public, symbol)
    from core.binance_futures import get_mark_price as binance_mark

    return binance_mark(clients, symbol)


def exchange_position_qty(clients: Any, symbol: str, side: str) -> float:
    if is_okx():
        from core.okx_futures import exchange_position_qty as okx_qty

        return okx_qty(clients, symbol, side)
    from core.binance_futures import exchange_position_qty as binance_qty

    return binance_qty(clients, symbol, side)


def place_market_reduce(
    clients: Any,
    *,
    symbol: str,
    side: str,
    quantity: float,
) -> dict[str, Any] | None:
    if is_okx():
        from core.okx_futures import place_market_reduce as okx_reduce

        return okx_reduce(clients, symbol=symbol, side=side, quantity=quantity)
    from core.binance_futures import place_market_reduce as binance_reduce

    return binance_reduce(clients, symbol=symbol, side=side, quantity=quantity)


def cancel_algo_order(clients: Any, algo_id: str | int, *, symbol: str | None = None) -> None:
    if is_okx():
        from core.okx_futures import cancel_algo_order as okx_cancel

        okx_cancel(clients.trade, algo_id, symbol=symbol)
        return
    from core.binance_futures import cancel_algo_order as binance_cancel

    binance_cancel(clients, algo_id, symbol=symbol)


def _cancel_algo(clients: Any, algo_id: str | int, *, symbol: str | None = None) -> None:
    cancel_algo_order(clients, algo_id, symbol=symbol)


def place_tp_algo(
    clients: Any,
    *,
    symbol: str,
    side: str,
    trigger_price: float,
    quantity: float,
) -> str | None:
    if quantity <= 0 or trigger_price <= 0:
        return None
    try:
        if is_okx():
            from core.okx_futures import place_tp_algo as okx_tp

            row = okx_tp(
                clients,
                symbol=symbol,
                side=side,
                trigger_price=trigger_price,
                quantity=quantity,
            )
        else:
            from core.binance_futures import place_algo_conditional_order

            exit_side = "SELL" if side == "long" else "BUY"
            row = place_algo_conditional_order(
                clients,
                symbol=symbol,
                side=exit_side,
                order_type="TAKE_PROFIT_MARKET",
                trigger_price=trigger_price,
                quantity=quantity,
            )
        algo_id = row.get("algoId")
        return str(algo_id) if algo_id else None
    except Exception as e:
        log.error(
            f"{symbol} 止盈掛單失敗 @ {trigger_price}: {format_futures_error(e)}"
        )
        return None


def replace_stop_algo(
    clients: Any,
    *,
    symbol: str,
    side: str,
    new_stop: float,
    quantity: float,
    stop_algo_id: str | None,
) -> str | None:
    """更新止損；OKX 優先 amend，失敗再 cancel+place。回傳新/既有 algoId。"""
    if quantity <= 0 or new_stop <= 0:
        return stop_algo_id

    if is_okx() and stop_algo_id:
        from core.okx_futures import amend_stop_algo

        try:
            amend_stop_algo(
                clients,
                stop_algo_id,
                symbol=symbol,
                new_stop=new_stop,
                quantity=quantity,
            )
            log.info(f"{symbol} 止損 amend @ {new_stop:.6g} qty={quantity}")
            return stop_algo_id
        except Exception as e:
            log.warning(f"{symbol} 止損 amend 失敗，改 cancel+place: {format_futures_error(e)}")
            _cancel_algo(clients, stop_algo_id, symbol=symbol)

    elif stop_algo_id:
        _cancel_algo(clients, stop_algo_id, symbol=symbol)

    try:
        if is_okx():
            from core.okx_futures import place_stop_algo as okx_sl

            row = okx_sl(
                clients,
                symbol=symbol,
                side=side,
                trigger_price=new_stop,
                quantity=quantity,
            )
        else:
            from core.binance_futures import place_algo_conditional_order

            exit_side = "SELL" if side == "long" else "BUY"
            row = place_algo_conditional_order(
                clients,
                symbol=symbol,
                side=exit_side,
                order_type="STOP_MARKET",
                trigger_price=new_stop,
                quantity=quantity,
            )
        algo_id = row.get("algoId")
        if algo_id:
            log.info(f"{symbol} 止損更新 @ {new_stop:.6g} qty={quantity}")
            return str(algo_id)
    except Exception as e:
        log.error(f"{symbol} 止損重掛失敗 @ {new_stop}: {format_futures_error(e)}")
    return None
