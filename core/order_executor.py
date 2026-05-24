"""自動下單：模擬盤 / 實盤（實盤需 API 與手動確認）。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import pandas as pd

from core.backtest_report import run_backtest
from core.market_data import MarketType, fetch_klines
from core.strategy_registry import STRATEGIES, get_strategy, scan_signals_for

_ORDERS_FILE = Path(__file__).resolve().parent.parent / "data" / "paper_orders.json"
_ORDERS_FILE.parent.mkdir(parents=True, exist_ok=True)


class OrderMode(str, Enum):
    PAPER = "paper"
    LIVE = "live"


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
    leverage: int = 1
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


def place_paper_order(req: OrderRequest) -> dict:
    orders = _load_orders()
    row = req.to_dict()
    row["status"] = "filled_paper"
    orders.append(row)
    _save_orders(orders)
    return row


def list_paper_orders(limit: int = 50) -> pd.DataFrame:
    orders = _load_orders()
    if not orders:
        return pd.DataFrame()
    return pd.DataFrame(orders[-limit:])


def scan_and_paper_trade(
    symbols: list[str],
    strategy_ids: list[str],
    market: MarketType = "futures",
    kline_limit: int = 500,
) -> list[dict]:
    """對多幣多策略掃描最新一根 K 的訊號，模擬下單。"""
    placed: list[dict] = []
    for sym in symbols:
        bin_sym = sym.replace("/", "").upper()
        for sid in strategy_ids:
            meta = get_strategy(sid)
            try:
                raw = fetch_klines(bin_sym, interval=meta.timeframe, limit=kline_limit, market=market)
            except Exception:
                continue
            prep = meta.prepare_df(raw)
            signals = scan_signals_for(sid, prep)
            if not signals:
                continue
            last = signals[-1]
            if last.bar_index < len(prep) - 2:
                continue
            plan = last.plan
            qty = getattr(plan, "position_size", 1.0) or 1.0
            req = OrderRequest(
                symbol=bin_sym,
                strategy_id=sid,
                side=last.side,
                entry=plan.entry,
                stop=plan.stop,
                quantity=float(qty),
                mode=OrderMode.PAPER,
                order_type="market",
                price=plan.entry,
                leverage=1,
                take_profit=_optional_tp(getattr(plan, "tp_final", None)),
                margin_type="cross",
            )
            placed.append(place_paper_order(req))
    return placed


def place_live_order(req: OrderRequest) -> dict:
    """
    實盤下單 — 需你在 .env 設定 API，且建議先用 testnet。
    目前僅串接現貨市價單示範；永續合約請改用 testnet 後再開啟。
    """
    if req.mode != OrderMode.LIVE:
        raise ValueError("mode 必須為 LIVE")

    from binance_client import create_client

    client = create_client()
    sym = req.symbol.replace("/", "")
    side = "BUY" if req.side == "long" else "SELL"
    # 現貨市價單（數量為 base asset）；正式上線前請改為永續 testnet
    result = client.new_order(
        symbol=sym,
        side=side,
        type="MARKET",
        quantity=round(req.quantity, 6),
    )
    row = req.to_dict()
    row["status"] = "filled_live"
    row["exchange_order_id"] = result.get("orderId")
    orders = _load_orders()
    orders.append(row)
    _save_orders(orders)
    return row
