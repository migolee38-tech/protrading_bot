"""自動下單：paper（本地）/ testnet（模擬倉）/ live（主網實盤）。"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import pandas as pd

from core.account_profiles import (
    AccountProfile,
    _LEGACY_ORDERS,
    load_profile,
    orders_file_for_profile,
    profile_configured,
)
from core.binance_credentials import ExecMode, credentials_hint, mode_label
from core.binance_futures import (
    FuturesSettings,
    _round_qty,
    _symbol_filters,
    calc_order_quantity,
    create_client,
    ensure_tradable_symbol,
    format_binance_error,
    resolve_leverage,
)
from risk import TradePlan, recalc_plan_for_fill
from core.position_manager import manage_positions_for_profile, register_live_position
from core.order_tags import build_client_order_id
from core.market_data import MarketType, fetch_klines
from core.strategy_registry import get_strategy, scan_signals_for, with_symbol

log = logging.getLogger(__name__)


class OrderMode(str, Enum):
    PAPER = "paper"
    TESTNET = "testnet"
    LIVE = "live"

    @classmethod
    def from_exec(cls, mode: ExecMode | str) -> "OrderMode":
        return cls(str(mode))


@dataclass
class OrderRequest:
    symbol: str
    strategy_id: str
    side: str
    entry: float
    stop: float
    quantity: float
    mode: OrderMode = OrderMode.PAPER
    account_id: str = "account1"
    order_type: str = "market"
    price: float | None = None
    leverage: int = 10
    take_profit: float | None = None
    margin_type: str = "cross"
    trade_plan: TradePlan | None = None
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        row: dict[str, Any] = {
            "symbol": self.symbol,
            "strategy_id": self.strategy_id,
            "side": self.side,
            "entry": self.entry,
            "stop": self.stop,
            "quantity": self.quantity,
            "order_type": self.order_type,
            "leverage": self.leverage,
            "margin_type": self.margin_type,
            "mode": self.mode.value,
            "account_id": self.account_id,
            "created_at": self.created_at,
            "status": "pending",
        }
        if self.price is not None:
            row["price"] = self.price
        if self.take_profit is not None:
            row["take_profit"] = self.take_profit
        return row

    @property
    def profile(self) -> AccountProfile:
        return load_profile(self.account_id, self.mode.value)


def _optional_tp(value: float | None) -> float | None:
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def _migrate_legacy_orders(profile: AccountProfile) -> list[dict]:
    """將舊版 paper_orders.json 遷移至 account1 對應 profile。"""
    if profile.account_id != "account1" or not _LEGACY_ORDERS.exists():
        return []
    try:
        with open(_LEGACY_ORDERS, encoding="utf-8") as f:
            rows = json.load(f)
    except Exception:
        return []
    if not isinstance(rows, list):
        return []
    mode_val = profile.network.value
    out: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_mode = str(row.get("mode", "paper"))
        row_acct = str(row.get("account_id", "account1")).lower()
        if row_mode == mode_val and row_acct == profile.account_id:
            out.append(row)
    return out


def _load_orders(profile: AccountProfile) -> list[dict]:
    path = orders_file_for_profile(profile)
    if path.exists():
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []

    legacy = _migrate_legacy_orders(profile)
    if legacy:
        _save_orders(profile, legacy)
    return legacy


def _save_orders(profile: AccountProfile, orders: list[dict]) -> None:
    path = orders_file_for_profile(profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(orders, f, ensure_ascii=False, indent=2)


def _append_order(profile: AccountProfile, row: dict) -> dict:
    orders = _load_orders(profile)
    orders.append(row)
    _save_orders(profile, orders)
    return row


def place_paper_order(req: OrderRequest) -> dict:
    row = req.to_dict()
    row["status"] = "filled_paper"
    return _append_order(req.profile, row)


def _plan_from_request(req: OrderRequest, qty: float) -> TradePlan:
    if req.trade_plan is not None:
        return req.trade_plan
    stub = TradePlan(
        side=req.side,
        entry=req.entry,
        stop=req.stop,
        r=abs(req.entry - req.stop),
        tp_1r=req.entry,
        tp_2r=req.entry,
        tp_final=float(req.take_profit or req.entry),
        stop_source="order",
        risk_pct=0.0,
        position_size=qty,
    )
    return recalc_plan_for_fill(stub, req.entry, req.strategy_id)


def place_futures_order(req: OrderRequest) -> dict:
    """永續合約下單（testnet 或 live）。"""
    if req.mode not in (OrderMode.TESTNET, OrderMode.LIVE):
        raise ValueError("place_futures_order 僅支援 testnet / live")

    profile = req.profile
    settings = FuturesSettings.from_profile(profile, leverage=req.leverage)
    if not settings.api_key or not settings.api_secret:
        raise ValueError(credentials_hint(req.mode, req.account_id))

    client = create_client(settings)
    lev = resolve_leverage(client, req.symbol.replace("/", "").upper(), settings)
    qty = req.quantity
    if qty <= 0:
        qty = calc_order_quantity(
            entry=req.entry,
            position_size=0,
            strategy_id=req.strategy_id,
            settings=settings,
            leverage=lev,
        )

    sym = req.symbol.replace("/", "").upper()
    ensure_tradable_symbol(client, sym, testnet=settings.testnet)
    client_order_id = build_client_order_id(req.strategy_id, sym)

    order_side = "BUY" if req.side == "long" else "SELL"
    try:
        client.change_leverage(symbol=sym, leverage=lev)
    except Exception as e:
        log.warning(f"{sym} 設定槓桿失敗: {format_binance_error(e)}")

    lot_step, _ = _symbol_filters(client, sym)
    qty = _round_qty(qty, lot_step)
    if qty <= 0:
        raise ValueError(f"{sym} 計算數量為 0")

    entry_kwargs: dict = {
        "symbol": sym,
        "side": order_side,
        "type": "MARKET",
        "quantity": qty,
        "newClientOrderId": client_order_id,
    }
    try:
        entry_result = client.new_order(**entry_kwargs)
    except Exception as e:
        raise ValueError(f"{sym} 市價進場失敗: {format_binance_error(e)}") from e

    plan = _plan_from_request(req, qty)
    protection_error: str | None = None
    live_state = None
    try:
        live_state = register_live_position(
            profile,
            client,
            strategy_id=req.strategy_id,
            symbol=sym,
            side=req.side,
            plan=plan,
            quantity=qty,
            entry_result=entry_result,
            exchange_order_id=str(entry_result.get("orderId", "")),
        )
    except Exception as e:
        protection_error = format_binance_error(e)
        log.error(f"{sym} 持倉登記/保護單失敗: {protection_error}")

    row = req.to_dict()
    row["quantity"] = qty
    row["leverage"] = lev
    if protection_error:
        row["status"] = "filled_naked"
        row["error"] = protection_error
    else:
        row["status"] = "filled_testnet" if req.mode == OrderMode.TESTNET else "filled_live"
    row["exchange_order_id"] = entry_result.get("orderId")
    row["client_order_id"] = entry_result.get("clientOrderId") or client_order_id
    if live_state and live_state.stop_algo_id:
        row["stop_algo_id"] = live_state.stop_algo_id
    if live_state and live_state.tp_algo_ids:
        row["take_profit_algo_ids"] = live_state.tp_algo_ids
    if protection_error:
        _append_order(profile, row)
        raise ValueError(protection_error)
    return _append_order(profile, row)


def place_spot_order(req: OrderRequest) -> dict:
    """現貨主網市價單（僅 live）。"""
    if req.mode != OrderMode.LIVE:
        raise ValueError("現貨下單目前僅支援 live 主網")

    from binance_client import create_client

    client = create_client()
    sym = req.symbol.replace("/", "")
    side = "BUY" if req.side == "long" else "SELL"
    result = client.new_order(
        symbol=sym,
        side=side,
        type="MARKET",
        quantity=round(req.quantity, 6),
    )
    row = req.to_dict()
    row["status"] = "filled_live"
    row["exchange_order_id"] = result.get("orderId")
    return _append_order(req.profile, row)


def place_order(req: OrderRequest, *, market: MarketType = "futures") -> dict:
    """依 mode 分派至 paper / 永續 testnet|live / 現貨 live。"""
    if req.mode == OrderMode.PAPER:
        return place_paper_order(req)

    if req.mode in (OrderMode.TESTNET, OrderMode.LIVE):
        if market == "futures":
            return place_futures_order(req)
        if req.mode == OrderMode.TESTNET:
            raise ValueError("現貨不支援 Testnet；請切換至永續市場或使用本地模擬。")
        return place_spot_order(req)

    raise ValueError(f"未知下單模式: {req.mode}")


def place_live_order(req: OrderRequest) -> dict:
    """向後相容：live 永續下單。"""
    req.mode = OrderMode.LIVE
    return place_futures_order(req)


def list_orders_for_profile(profile: AccountProfile, limit: int = 500) -> pd.DataFrame:
    orders = _load_orders(profile)
    if not orders:
        return pd.DataFrame()
    return pd.DataFrame(orders[-limit:])


def list_paper_orders(
    limit: int = 50,
    *,
    account_id: str = "account1",
    mode: ExecMode | str = ExecMode.PAPER,
) -> pd.DataFrame:
    """向後相容；建議改用 list_orders_for_profile。"""
    profile = load_profile(account_id, mode)
    return list_orders_for_profile(profile, limit=limit)


def _signal_from_scan(
    sym: str,
    sid: str,
    prep: pd.DataFrame,
    mode: OrderMode,
    leverage: int,
    account_id: str,
) -> OrderRequest | None:
    signals = scan_signals_for(sid, prep)
    if not signals:
        return None
    last = signals[-1]
    if last.bar_index < len(prep) - 2:
        return None

    plan = last.plan
    bin_sym = sym.replace("/", "").upper()
    pos_size = float(getattr(plan, "position_size", 0) or 0)
    if mode == OrderMode.PAPER:
        qty = pos_size if sid == "donchian" and pos_size > 0 else max(pos_size, 0.001)
    else:
        qty = pos_size if sid == "donchian" and pos_size > 0 else 0.0

    return OrderRequest(
        symbol=bin_sym,
        strategy_id=sid,
        side=last.side,
        entry=float(plan.entry),
        stop=float(plan.stop),
        quantity=qty,
        mode=mode,
        account_id=account_id,
        order_type="market",
        price=float(plan.entry),
        leverage=leverage,
        take_profit=_optional_tp(getattr(plan, "tp_final", None)),
        margin_type="cross",
        trade_plan=plan,
    )


def scan_and_execute(
    symbols: list[str],
    strategy_ids: list[str],
    mode: ExecMode | OrderMode | str = OrderMode.PAPER,
    market: MarketType = "futures",
    kline_limit: int = 500,
    leverage: int = 10,
    account_id: str = "account1",
) -> list[dict]:
    """對多幣多策略掃描最新訊號並依模式下單。"""
    if isinstance(mode, str):
        order_mode = OrderMode(mode)
    elif isinstance(mode, ExecMode):
        order_mode = OrderMode.from_exec(mode)
    else:
        order_mode = mode

    profile = load_profile(account_id, order_mode.value)
    if order_mode != OrderMode.PAPER and not profile_configured(profile):
        raise ValueError(credentials_hint(order_mode, account_id))

    placed: list[dict] = []
    for sym in symbols:
        bin_sym = sym.replace("/", "").upper()
        for sid in strategy_ids:
            meta = get_strategy(sid)
            try:
                raw = fetch_klines(bin_sym, interval=meta.timeframe, limit=kline_limit, market=market)
            except Exception:
                continue
            prep = meta.prepare_df(with_symbol(raw, bin_sym, kline_limit=kline_limit))
            req = _signal_from_scan(sym, sid, prep, order_mode, leverage, account_id)
            if req is None:
                continue
            try:
                placed.append(place_order(req, market=market))
            except Exception as e:
                log.error(
                    f"{profile.display_name} 下單失敗 {bin_sym} {sid}: {format_binance_error(e)}"
                )
    if order_mode != OrderMode.PAPER:
        try:
            manage_positions_for_profile(profile)
        except Exception as e:
            log.error(f"{profile.display_name} 持倉管理失敗: {format_binance_error(e)}")
    return placed


def scan_and_paper_trade(
    symbols: list[str],
    strategy_ids: list[str],
    market: MarketType = "futures",
    kline_limit: int = 500,
    account_id: str = "account1",
) -> list[dict]:
    """向後相容：僅 paper 模式。"""
    return scan_and_execute(
        symbols,
        strategy_ids,
        mode=OrderMode.PAPER,
        market=market,
        kline_limit=kline_limit,
        account_id=account_id,
    )
