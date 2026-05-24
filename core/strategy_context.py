"""暫時切換 config.STRATEGY，供多策略並行回測。"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import config as cfg


@contextmanager
def use_strategy(strategy_id: str) -> Iterator[None]:
    old = cfg.STRATEGY
    cfg.STRATEGY = strategy_id
    try:
        yield
    finally:
        cfg.STRATEGY = old
