"""同幣種一次只能開一單。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Union

import config as cfg
from cooldown import CooldownManager
from position import Position
from position_donchian import DonchianPosition
from risk import TradePlan

ActivePosition = Union[Position, DonchianPosition]


@dataclass
class Portfolio:
    positions: dict[str, ActivePosition] = field(default_factory=dict)

    def has_position(self, symbol: str) -> bool:
        pos = self.positions.get(symbol)
        return pos is not None and not pos.closed and pos.size > 0

    def can_open(self, symbol: str, cooldown: CooldownManager) -> bool:
        return not self.has_position(symbol) and not cooldown.is_blocked(symbol)

    def open_position(
        self,
        symbol: str,
        plan: TradePlan,
        size: float | None = None,
    ) -> ActivePosition:
        if self.has_position(symbol):
            raise RuntimeError(f"{symbol} 已有持倉，無法重複開倉")

        if cfg.STRATEGY == "donchian":
            qty = size if size is not None else plan.position_size
            pos: ActivePosition = DonchianPosition(
                symbol=symbol, plan=plan, initial_size=qty, size=qty
            )
        else:
            qty = 1.0 if size is None else size
            pos = Position(symbol=symbol, plan=plan, initial_size=qty, size=qty)
        self.positions[symbol] = pos
        return pos

    def get(self, symbol: str) -> ActivePosition | None:
        return self.positions.get(symbol)

    def close_symbol(self, symbol: str) -> None:
        pos = self.positions.get(symbol)
        if pos:
            pos.closed = True
            pos.size = 0.0
