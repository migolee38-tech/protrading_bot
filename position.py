"""持倉：分批止盈、套保、移動停利。"""

from __future__ import annotations

from dataclasses import dataclass, field

import config as cfg
from risk import TradePlan


@dataclass
class Position:
    symbol: str
    plan: TradePlan
    initial_size: float = 1.0
    size: float = 1.0
    stop: float = field(init=False)
    reduced_1r: bool = False
    reduced_2r: bool = False
    trailing_active: bool = False
    peak_r: float = 0.0
    closed: bool = False

    def __post_init__(self) -> None:
        self.stop = self.plan.stop

    def _hit_stop(self, high: float, low: float) -> bool:
        if self.plan.side == "long":
            return low <= self.stop
        return high >= self.stop

    def _update_peak(self, high: float, low: float) -> None:
        p = self.plan
        if p.r <= 0:
            return
        if p.side == "long":
            self.peak_r = max(self.peak_r, (high - p.entry) / p.r)
        else:
            self.peak_r = max(self.peak_r, (p.entry - low) / p.r)

    def _apply_trailing_stop(self) -> None:
        """自 1:2 起：價格每多 0.5R，停利上移 0.5R（peak_r - 1R）。"""
        if not self.trailing_active or self.peak_r < cfg.RR_PARTIAL_2:
            return

        p = self.plan
        trail_r = max(cfg.RR_PARTIAL_1, self.peak_r - cfg.RR_PARTIAL_1)
        if p.side == "long":
            new_stop = p.entry + trail_r * p.r
            self.stop = max(self.stop, new_stop)
        else:
            new_stop = p.entry - trail_r * p.r
            self.stop = min(self.stop, new_stop)

    def _reduce(self, pct: float, label: str) -> str:
        cut = self.initial_size * pct
        self.size = max(0.0, self.size - cut)
        return f"{label} (-{pct:.0%}, 剩餘={self.size:.2f})"

    def on_bar(self, high: float, low: float) -> list[str]:
        """處理一根 K 的持倉更新，回傳事件列表。"""
        if self.closed or self.size <= 0:
            return []

        events: list[str] = []
        p = self.plan

        if self._hit_stop(high, low):
            self.closed = True
            self.size = 0.0
            events.append("stop_loss")
            return events

        if p.side == "long":
            if high >= p.tp_final:
                self.closed = True
                self.size = 0.0
                events.append("final_tp_10r")
                return events

            if not self.reduced_2r and high >= p.tp_2r:
                events.append(self._reduce(cfg.REDUCE_AT_2R_PCT, "partial_tp_2r"))
                self.stop = p.stop_1r
                self.trailing_active = True
                self.reduced_2r = True
                events.append("hedge_to_1r")

            if not self.reduced_1r and high >= p.tp_1r:
                events.append(self._reduce(cfg.REDUCE_AT_1R_PCT, "partial_tp_1r"))
                self.stop = p.entry
                self.reduced_1r = True
                events.append("hedge_to_entry")
        else:
            if low <= p.tp_final:
                self.closed = True
                self.size = 0.0
                events.append("final_tp_10r")
                return events

            if not self.reduced_2r and low <= p.tp_2r:
                events.append(self._reduce(cfg.REDUCE_AT_2R_PCT, "partial_tp_2r"))
                self.stop = p.stop_1r
                self.trailing_active = True
                self.reduced_2r = True
                events.append("hedge_to_1r")

            if not self.reduced_1r and low <= p.tp_1r:
                events.append(self._reduce(cfg.REDUCE_AT_1R_PCT, "partial_tp_1r"))
                self.stop = p.entry
                self.reduced_1r = True
                events.append("hedge_to_entry")

        self._update_peak(high, low)
        self._apply_trailing_stop()

        if self._hit_stop(high, low):
            self.closed = True
            self.size = 0.0
            events.append("stop_loss")
            return events

        return events

    def is_win_exit(self, last_events: list[str]) -> bool:
        return any(e.startswith("partial_tp") or e == "final_tp_10r" for e in last_events)
