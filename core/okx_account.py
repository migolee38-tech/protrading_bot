"""OKX 帳戶資料：轉成 futures_account 使用的 Binance 相容欄位。"""

from __future__ import annotations

from typing import Any

from core.okx_futures import (
    OkxClients,
    OkxFuturesSettings,
    contracts_to_coin,
    create_clients,
    ensure_ok_response,
    fetch_open_algo_orders,
    fetch_swap_positions,
    fetch_usdt_balance,
    from_inst_id,
    get_instrument,
    get_pos_mode,
    is_hedge_mode,
    to_inst_id,
)


def _float(val: Any, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def create_account_clients(settings: OkxFuturesSettings) -> OkxClients:
    return create_clients(settings)


def fetch_wallet_balances(clients: OkxClients) -> tuple[float, float]:
    return fetch_usdt_balance(clients.account)


def positions_as_binance_rows(clients: OkxClients) -> list[dict[str, Any]]:
    """持倉 → Binance get_position_risk 相容格式（positionAmt 為幣數）。"""
    rows = fetch_swap_positions(clients.account)
    pos_mode = get_pos_mode(clients.account, clients.settings)
    out: list[dict[str, Any]] = []
    for row in rows:
        inst_id = str(row.get("instId", ""))
        sym = from_inst_id(inst_id)
        try:
            instrument = get_instrument(clients.public, sym)
        except Exception:
            instrument = {"ctVal": "1"}
        contracts = abs(_float(row.get("pos")))
        if contracts <= 0:
            continue
        coin_qty = contracts_to_coin(contracts, instrument)
        pos_side = str(row.get("posSide", "net")).lower()
        if is_hedge_mode(pos_mode):
            signed = coin_qty if pos_side == "long" else -coin_qty
        else:
            signed = coin_qty if _float(row.get("pos")) > 0 else -coin_qty
        out.append(
            {
                "symbol": sym,
                "positionAmt": signed,
                "entryPrice": _float(row.get("avgPx")),
                "markPrice": _float(row.get("markPx")),
                "unRealizedProfit": _float(row.get("upl")),
                "leverage": int(_float(row.get("lever"), 1)),
                "marginType": str(row.get("mgnMode", "cross")),
                "liquidationPrice": _float(row.get("liqPx")),
            }
        )
    return out


def _contracts_to_coin_qty(clients: OkxClients, symbol: str, contracts: Any) -> float:
    sz = _float(contracts)
    if sz <= 0:
        return 0.0
    try:
        instrument = get_instrument(clients.public, symbol)
        return contracts_to_coin(sz, instrument)
    except Exception:
        return sz


def _normalize_okx_algo_open_order(row: dict[str, Any]) -> dict[str, Any]:
    sym = from_inst_id(str(row.get("instId", "")))
    return {
        "symbol": sym,
        "type": str(row.get("ordType") or row.get("algoOrdType") or "conditional").upper(),
        "side": str(row.get("side", "")).upper(),
        "price": _float(row.get("px")),
        "stopPrice": _float(row.get("triggerPx") or row.get("tpTriggerPx") or row.get("slTriggerPx")),
        "origQty": _float(row.get("sz")),
        "executedQty": 0.0,
        "reduceOnly": True,
        "status": str(row.get("state") or row.get("algoStatus") or ""),
        "orderId": row.get("algoId", ""),
        "clientOrderId": row.get("algoClOrdId", ""),
        "time": row.get("cTime") or row.get("createTime"),
    }


def _normalize_okx_open_order(row: dict[str, Any], clients: OkxClients) -> dict[str, Any]:
    sym = from_inst_id(str(row.get("instId", "")))
    contracts = _float(row.get("sz"))
    filled = _float(row.get("fillSz"))
    return {
        "symbol": sym,
        "type": str(row.get("ordType", "")).upper(),
        "side": str(row.get("side", "")).upper(),
        "price": _float(row.get("px")),
        "stopPrice": _float(row.get("slTriggerPx") or row.get("tpTriggerPx")),
        "origQty": _contracts_to_coin_qty(clients, sym, contracts),
        "executedQty": _contracts_to_coin_qty(clients, sym, filled),
        "reduceOnly": str(row.get("reduceOnly", "")).lower() == "true",
        "status": str(row.get("state", "")),
        "orderId": row.get("ordId", ""),
        "clientOrderId": row.get("clOrdId", ""),
        "time": row.get("cTime"),
    }


def fetch_all_open_orders(clients: OkxClients, symbols: set[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        response = ensure_ok_response(
            clients.trade.get_order_list(instType="SWAP", state="live"),
            context="查詢掛單",
        )
        for row in response.get("data") or []:
            sym = from_inst_id(str(row.get("instId", "")))
            if symbols and sym not in symbols:
                continue
            rows.append(_normalize_okx_open_order(row, clients))
    except Exception:
        for sym in sorted(symbols):
            try:
                response = ensure_ok_response(
                    clients.trade.get_order_list(
                        instType="SWAP",
                        instId=to_inst_id(sym),
                        state="live",
                    ),
                    context=f"查詢掛單 {sym}",
                )
                for row in response.get("data") or []:
                    rows.append(_normalize_okx_open_order(row, clients))
            except Exception:
                continue

    for algo in fetch_open_algo_orders(clients.trade):
        sym = from_inst_id(str(algo.get("instId", "")))
        if symbols and sym not in symbols:
            continue
        rows.append(_normalize_okx_algo_open_order(algo))
    return rows


def fetch_orders_by_symbol(
    clients: OkxClients,
    symbols: set[str],
    *,
    order_limit: int = 500,
) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for sym in sorted(symbols):
        try:
            response = ensure_ok_response(
                clients.trade.get_orders_history(
                    instType="SWAP",
                    instId=to_inst_id(sym),
                    limit=str(min(order_limit, 100)),
                ),
                context=f"查詢歷史訂單 {sym}",
            )
            rows = response.get("data") or []
            if rows:
                out[sym] = [
                    {
                        "orderId": r.get("ordId", ""),
                        "clientOrderId": r.get("clOrdId", ""),
                        "origClientOrderId": r.get("clOrdId", ""),
                        "side": str(r.get("side", "")).upper(),
                        "type": str(r.get("ordType", "")).upper(),
                        "status": str(r.get("state", "")).upper(),
                        "avgPrice": _float(r.get("avgPx")),
                        "price": _float(r.get("px")),
                        "time": r.get("cTime"),
                        "updateTime": r.get("uTime"),
                    }
                    for r in rows
                ]
        except Exception:
            continue
    return out


def fetch_account_trades(
    clients: OkxClients,
    symbols: set[str],
    *,
    trade_limit_per_symbol: int = 50,
) -> list[dict[str, Any]]:
    trade_rows: list[dict[str, Any]] = []
    for sym in sorted(symbols):
        try:
            response = ensure_ok_response(
                clients.trade.get_fills_history(
                    instType="SWAP",
                    instId=to_inst_id(sym),
                    limit=str(min(trade_limit_per_symbol, 100)),
                ),
                context=f"查詢成交 {sym}",
            )
            for r in response.get("data") or []:
                contracts = _float(r.get("fillSz"))
                trade_rows.append(
                    {
                        "symbol": sym,
                        "orderId": r.get("ordId", ""),
                        "clientOrderId": r.get("clOrdId", ""),
                        "side": str(r.get("side", "")).upper(),
                        "price": _float(r.get("fillPx")),
                        "qty": _contracts_to_coin_qty(clients, sym, contracts),
                        "realizedPnl": _float(r.get("fillPnl")),
                        "commission": abs(_float(r.get("fee"))),
                        "commissionAsset": r.get("feeCcy", "USDT"),
                        "time": r.get("ts"),
                        "quoteQty": 0.0,
                    }
                )
        except Exception:
            continue
    return trade_rows


def _map_bill_income_type(row: dict[str, Any]) -> str:
    text = " ".join(
        str(row.get(k, "") or "")
        for k in ("type", "subType", "notes")
    ).lower()
    if "funding" in text:
        return "FUNDING_FEE"
    if "fee" in text or "commission" in text:
        return "COMMISSION"
    if any(k in text for k in ("pnl", "profit", "loss", "close", "liquidation")):
        return "REALIZED_PNL"
    bal = _float(row.get("balChg"))
    if bal != 0:
        return "REALIZED_PNL"
    return str(row.get("type", "OTHER")).upper()


def fetch_income_history(
    clients: OkxClients,
    *,
    income_limit: int = 200,
) -> list[dict[str, Any]]:
    try:
        response = ensure_ok_response(
            clients.account.get_account_bills(
                instType="SWAP",
                limit=str(min(income_limit, 100)),
            ),
            context="查詢帳單",
        )
    except Exception:
        return []

    rows: list[dict[str, Any]] = []
    for r in response.get("data") or []:
        inst_id = str(r.get("instId", "") or "")
        sym = from_inst_id(inst_id) if inst_id else ""
        income = _float(r.get("balChg"))
        if income == 0:
            income = _float(r.get("pnl")) or -abs(_float(r.get("fee")))
        rows.append(
            {
                "time": r.get("ts"),
                "symbol": sym,
                "incomeType": _map_bill_income_type(r),
                "income": income,
                "asset": r.get("ccy", "USDT"),
                "info": str(r.get("type", "")),
                "tradeId": r.get("billId", ""),
            }
        )
    return rows


def format_okx_api_error(exc: Exception) -> str:
    from core.okx_futures import format_okx_error

    return format_okx_error(exc)
