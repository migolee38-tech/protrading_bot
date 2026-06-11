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

from core.binance_credentials import ExecMode, credentials_configured, credentials_hint, mode_label
from core.binance_futures import (
    FuturesSettings,
    calc_order_quantity,
    create_client,
    place_bracket_order,
    resolve_leverage,
)
from core.market_data import MarketType, fetch_klines
from core.strategy_registry import get_strategy, scan_signals_for, with_symbol

log = logging.getLogger(__name__)

_ORDERS_FILE = Path(__file__).resolve().parent.parent / "data" / "paper_orders.json"
_ORDERS_FILE.parent.mkdir(parents=True, exist_ok=True)


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
    order_type: str = "market"
    price: float | None = None
    leverage: int = 10
    take_profit: float | None = None
    margin_type: str = "cross"
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
            "created_at": self.created_at,
            "status": "pending",
        }
        if self.price is not None:
            row["price"] = self.price
        if self.take_profit is not None:
            row["take_profit"] = self.take_profit
        return row


def _optional_tp(value: float | None) -> float | None:
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def _load_orders() -> list[dict]:
    if not _ORDERS_FILE.exists():
        return []
    with open(_ORDERS_FILE, encoding="utf-8") as f:
        return json.load(f)


def _save_orders(orders: list[dict]) -> None:
    with open(_ORDERS_FILE, "w", encoding="utf-8") as f:
        json.dump(orders, f, ensure_ascii=False, indent=2)


def _append_order(row: dict) -> dict:
    orders = _load_orders()
    orders.append(row)
    _save_orders(orders)
    return row


def place_paper_order(req: OrderRequest) -> dict:
    row = req.to_dict()
    row["status"] = "filled_paper"
    return _append_order(row)


def place_futures_order(req: OrderRequest) -> dict:
    """永續合約下單（testnet 或 live）。"""
    if req.mode not in (OrderMode.TESTNET, OrderMode.LIVE):
        raise ValueError("place_futures_order 僅支援 testnet / live")

    settings = FuturesSettings.from_exec_mode(req.mode, leverage=req.leverage)
    if not settings.api_key or not settings.api_secret:
        raise ValueError(credentials_hint(req.mode))

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
    result = place_bracket_order(
        client,
        symbol=sym,
        side=req.side,
        quantity=qty,
        stop=req.stop,
        take_profit=req.take_profit,
        leverage=lev,
    )

    row = req.to_dict()
    row["quantity"] = qty
    row["leverage"] = lev
    row["status"] = "filled_testnet" if req.mode == OrderMode.TESTNET else "filled_live"
    row["exchange_order_id"] = result.get("orderId")
    return _append_order(row)


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
    return _append_order(row)


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


def list_paper_orders(limit: int = 50) -> pd.DataFrame:
    orders = _load_orders()
    if not orders:
        return pd.DataFrame()
    return pd.DataFrame(orders[-limit:])


def _signal_from_scan(
    sym: str,
    sid: str,
    prep: pd.DataFrame,
    mode: OrderMode,
    leverage: int,
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
        order_type="market",
        price=float(plan.entry),
        leverage=leverage,
        take_profit=_optional_tp(getattr(plan, "tp_final", None)),
        margin_type="cross",
    )


def scan_and_execute(
    symbols: list[str],
    strategy_ids: list[str],
    mode: ExecMode | OrderMode | str = OrderMode.PAPER,
    market: MarketType = "futures",
    kline_limit: int = 500,
    leverage: int = 10,
) -> list[dict]:
    """對多幣多策略掃描最新訊號並依模式下單。"""
    if isinstance(mode, str):
        order_mode = OrderMode(mode)
    elif isinstance(mode, ExecMode):
        order_mode = OrderMode.from_exec(mode)
    else:
        order_mode = mode

    if order_mode != OrderMode.PAPER and not credentials_configured(order_mode):
        raise ValueError(credentials_hint(order_mode))

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
            req = _signal_from_scan(sym, sid, prep, order_mode, leverage)
            if req is None:
                continue
            try:
                placed.append(place_order(req, market=market))
            except Exception as e:
                log.error(f"{order_mode.value} 下單失敗 {bin_sym} {sid}: {e}")
    return placed


def scan_and_paper_trade(
    symbols: list[str],
    strategy_ids: list[str],
    market: MarketType = "futures",
    kline_limit: int = 500,
) -> list[dict]:
    """向後相容：僅 paper 模式。"""
    return scan_and_execute(
        symbols,
        strategy_ids,
        mode=OrderMode.PAPER,
        market=market,
        kline_limit=kline_limit,
    )
