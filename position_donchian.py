"""唐奇安持倉：1:2 / 1:5 分批、3R 鎖利、2R 追蹤間距、10R 全平。"""

from __future__ import annotations

from dataclasses import dataclass, field

import config as cfg
from risk import TradePlan


@dataclass
class DonchianPosition:
    symbol: str
    plan: TradePlan
    initial_size: float = 1.0
    size: float = 1.0
    stop: float = field(init=False)
    reduced_tp1: bool = False
    reduced_tp2: bool = False
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
        """獲利 ≥5R 後：止損維持在 peak_r - 2R，且不低於 3R。"""
        if not self.trailing_active:
            return

        p = self.plan
        floor_r = cfg.DONCHIAN_STOP_AFTER_TP2_R
        trail_r = max(floor_r, self.peak_r - cfg.DONCHIAN_TRAIL_OFFSET_R)
        if p.side == "long":
            new_stop = p.entry + trail_r * p.r
            self.stop = max(self.stop, new_stop)
        else:
            new_stop = p.entry - trail_r * p.r
            self.stop = min(self.stop, new_stop)

    def _reduce_of_initial(self, pct: float, label: str) -> str:
        cut = self.initial_size * pct
        self.size = max(0.0, self.size - cut)
        return f"{label} (-{pct:.0%}起始倉, 剩餘={self.size:.4f})"

    def _reduce_of_current(self, pct: float, label: str) -> str:
        cut = self.size * pct
        self.size = max(0.0, self.size - cut)
        return f"{label} (-{pct:.0%}剩餘倉, 剩餘={self.size:.4f})"

    def on_bar(self, high: float, low: float) -> list[str]:
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

            if not self.reduced_tp1 and high >= p.tp_1r:
                events.append(
                    self._reduce_of_initial(
                        cfg.DONCHIAN_REDUCE_TP1_PCT, "partial_tp_2r_50pct"
                    )
                )
                self.stop = p.entry
                self.reduced_tp1 = True
                events.append("hedge_to_entry")

            if not self.reduced_tp2 and high >= p.tp_2r:
                events.append(
                    self._reduce_of_current(
                        cfg.DONCHIAN_REDUCE_TP2_PCT, "partial_tp_5r_50pct"
                    )
                )
                self.stop = p.stop_3r
                self.trailing_active = True
                self.reduced_tp2 = True
                events.append("hedge_to_3r")
        else:
            if low <= p.tp_final:
                self.closed = True
                self.size = 0.0
                events.append("final_tp_10r")
                return events

            if not self.reduced_tp1 and low <= p.tp_1r:
                events.append(
                    self._reduce_of_initial(
                        cfg.DONCHIAN_REDUCE_TP1_PCT, "partial_tp_2r_50pct"
                    )
                )
                self.stop = p.entry
                self.reduced_tp1 = True
                events.append("hedge_to_entry")

            if not self.reduced_tp2 and low <= p.tp_2r:
                events.append(
                    self._reduce_of_current(
                        cfg.DONCHIAN_REDUCE_TP2_PCT, "partial_tp_5r_50pct"
                    )
                )
                self.stop = p.stop_3r
                self.trailing_active = True
                self.reduced_tp2 = True
                events.append("hedge_to_3r")

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
