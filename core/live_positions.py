"""實盤持倉狀態持久化（testnet / live）。"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from core.account_profiles import AccountProfile, live_positions_file_for_profile
from risk import TradePlan


@dataclass
class LivePositionState:
    """單筆實盤持倉的出場管理狀態。"""

    position_id: str
    symbol: str
    strategy_id: str
    side: str
    entry_price: float
    initial_qty: float
    remaining_qty: float
    stop: float
    plan_entry: float
    plan_stop: float
    plan_r: float
    tp_1r: float
    tp_2r: float
    tp_final: float
    account_id: str = "account1"
    exchange_order_id: str = ""
    stop_algo_id: str | None = None
    tp_algo_ids: list[str] = field(default_factory=list)
    reduced_1r: bool = False
    reduced_2r: bool = False
    trailing_active: bool = False
    peak_r: float = 0.0
    stage: int = 0
    closed: bool = False
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_trade_plan(self) -> TradePlan:
        return TradePlan(
            side=self.side,
            entry=self.entry_price,
            stop=self.stop,
            r=self.plan_r,
            tp_1r=self.tp_1r,
            tp_2r=self.tp_2r,
            tp_final=self.tp_final,
            stop_source="live",
            risk_pct=abs(self.entry_price - self.plan_stop) / max(self.entry_price, 1e-12),
            position_size=self.initial_qty,
        )

    def sync_plan_levels(self, plan: TradePlan) -> None:
        self.entry_price = plan.entry
        self.plan_entry = plan.entry
        self.plan_stop = plan.stop
        self.plan_r = plan.r
        self.stop = plan.stop
        self.tp_1r = plan.tp_1r
        self.tp_2r = plan.tp_2r
        self.tp_final = plan.tp_final

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> "LivePositionState":
        tp_ids = row.get("tp_algo_ids") or []
        return cls(
            position_id=str(row["position_id"]),
            symbol=str(row["symbol"]).upper(),
            strategy_id=str(row["strategy_id"]),
            side=str(row["side"]),
            entry_price=float(row["entry_price"]),
            initial_qty=float(row["initial_qty"]),
            remaining_qty=float(row["remaining_qty"]),
            stop=float(row["stop"]),
            plan_entry=float(row.get("plan_entry", row["entry_price"])),
            plan_stop=float(row.get("plan_stop", row["stop"])),
            plan_r=float(row.get("plan_r", 0)),
            tp_1r=float(row.get("tp_1r", 0)),
            tp_2r=float(row.get("tp_2r", 0)),
            tp_final=float(row.get("tp_final", 0)),
            account_id=str(row.get("account_id", "account1")),
            exchange_order_id=str(row.get("exchange_order_id", "")),
            stop_algo_id=str(row["stop_algo_id"]) if row.get("stop_algo_id") else None,
            tp_algo_ids=[str(x) for x in tp_ids],
            reduced_1r=bool(row.get("reduced_1r", False)),
            reduced_2r=bool(row.get("reduced_2r", False)),
            trailing_active=bool(row.get("trailing_active", False)),
            peak_r=float(row.get("peak_r", 0)),
            stage=int(row.get("stage", 0)),
            closed=bool(row.get("closed", False)),
            created_at=str(row.get("created_at", "")),
        )


def new_position_id(strategy_id: str, symbol: str) -> str:
    return f"{strategy_id}:{symbol}:{uuid.uuid4().hex[:10]}"


def load_live_positions(profile: AccountProfile) -> list[LivePositionState]:
    path = live_positions_file_for_profile(profile)
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        return []
    return [LivePositionState.from_dict(row) for row in data if isinstance(row, dict)]


def save_live_positions(profile: AccountProfile, positions: list[LivePositionState]) -> None:
    path = live_positions_file_for_profile(profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump([p.to_dict() for p in positions], f, ensure_ascii=False, indent=2)


def upsert_position(
    profile: AccountProfile,
    position: LivePositionState,
) -> LivePositionState:
    rows = load_live_positions(profile)
    out: list[LivePositionState] = []
    found = False
    for row in rows:
        if row.position_id == position.position_id:
            if not position.closed:
                out.append(position)
            found = True
        elif not row.closed:
            out.append(row)
    if not found and not position.closed:
        out.append(position)
    save_live_positions(profile, out)
    return position


def list_open_positions(profile: AccountProfile) -> list[LivePositionState]:
    return [p for p in load_live_positions(profile) if not p.closed and p.remaining_qty > 0]
