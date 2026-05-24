"""同幣種 24h 內連續止損 2 次 → 待機 24h。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config as cfg


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class SymbolCooldownState:
    consecutive_sl: int = 0
    cooldown_until: datetime | None = None


@dataclass
class CooldownManager:
    symbols: dict[str, SymbolCooldownState] = field(default_factory=dict)
    state_path: Path = field(default_factory=lambda: Path(cfg.STATE_FILE))

    def is_blocked(self, symbol: str, now: datetime | None = None) -> bool:
        now = now or _utc_now()
        state = self.symbols.get(symbol)
        if state is None or state.cooldown_until is None:
            return False
        if now >= state.cooldown_until:
            state.cooldown_until = None
            state.consecutive_sl = 0
            return False
        return True

    def record_stop_loss(self, symbol: str, now: datetime | None = None) -> bool:
        """
        記錄止損。若觸發 24h 冷卻回傳 True。
        僅統計 SL_WINDOW_HOURS 內的連續止損。
        """
        now = now or _utc_now()
        state = self.symbols.setdefault(symbol, SymbolCooldownState())
        state.consecutive_sl += 1

        if state.consecutive_sl >= cfg.MAX_CONSECUTIVE_SL:
            state.cooldown_until = now + timedelta(hours=cfg.COOLDOWN_HOURS)
            state.consecutive_sl = 0
            return True
        return False

    def record_win(self, symbol: str) -> None:
        """止盈出場後重置連續止損計數。"""
        state = self.symbols.setdefault(symbol, SymbolCooldownState())
        state.consecutive_sl = 0

    def save(self) -> None:
        payload = {}
        for sym, st in self.symbols.items():
            payload[sym] = {
                "consecutive_sl": st.consecutive_sl,
                "cooldown_until": st.cooldown_until.isoformat() if st.cooldown_until else None,
            }
        self.state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def load(self) -> None:
        if not self.state_path.exists():
            return
        raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        for sym, data in raw.items():
            until = data.get("cooldown_until")
            self.symbols[sym] = SymbolCooldownState(
                consecutive_sl=int(data.get("consecutive_sl", 0)),
                cooldown_until=datetime.fromisoformat(until) if until else None,
            )
