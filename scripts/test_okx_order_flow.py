#!/usr/bin/env python3
"""Step 3：透過 order_executor + position_manager 測試 OKX Demo 下單流程。"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.env_bootstrap import load_project_env

load_project_env()
os.environ.setdefault("EXCHANGE", "okx")

from core.account_profiles import load_profile  # noqa: E402
from core.binance_credentials import ExecMode  # noqa: E402
from core.futures_execution import (  # noqa: E402
    create_futures_clients,
    exchange_position_qty,
    format_futures_error,
    place_market_reduce,
    settings_for_profile,
)
from core.live_positions import list_open_positions  # noqa: E402
from core.order_executor import OrderMode, OrderRequest, place_futures_order  # noqa: E402
from core.position_manager import manage_positions_for_profile  # noqa: E402

log = logging.getLogger("test_okx_order_flow")


def _setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")


def _cleanup_open(profile, clients) -> None:
    for state in list_open_positions(profile):
        qty = exchange_position_qty(clients, state.symbol, state.side)
        if qty > 0:
            place_market_reduce(
                clients, symbol=state.symbol, side=state.side, quantity=qty
            )
        state.closed = True
        state.remaining_qty = 0.0
        from core.live_positions import upsert_position

        upsert_position(profile, state)


def run(account: str, *, execute: bool) -> int:
    profile = load_profile(account, ExecMode.TESTNET)
    if not execute:
        log.info(f"dry-run：將測試 {profile.display_name} 的 place_futures_order + manage")
        return 0

    req = OrderRequest(
        symbol="BTCUSDT",
        strategy_id="ema",
        side="long",
        entry=65000.0,
        stop=63000.0,
        quantity=0.0002,
        mode=OrderMode.TESTNET,
        account_id=account,
        leverage=5,
    )
    settings = settings_for_profile(profile, leverage=5)
    clients = create_futures_clients(settings)

    try:
        row = place_futures_order(req)
        log.info(f"下單結果 status={row.get('status')} stop_algo={row.get('stop_algo_id')}")

        open_pos = list_open_positions(profile)
        log.info(f"本地開倉數: {len(open_pos)}")
        if open_pos:
            st = open_pos[0]
            log.info(
                f"  {st.symbol} {st.side} qty={st.remaining_qty} "
                f"sl_algo={st.stop_algo_id} tp_algos={st.tp_algo_ids}"
            )

        managed = manage_positions_for_profile(profile, settings)
        log.info(f"持倉管理輪詢: 更新 {managed} 筆")

        _cleanup_open(profile, clients)
        log.info("✅ Step 3 整合測試完成並已平倉")
        return 0
    except Exception as e:
        log.error(f"❌ 失敗: {format_futures_error(e)}")
        try:
            _cleanup_open(profile, clients)
        except Exception as cleanup_exc:
            log.error(f"清理失敗: {format_futures_error(cleanup_exc)}")
        return 1


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--account", default="account1")
    p.add_argument("--execute", action="store_true")
    args = p.parse_args()
    _setup_logging()
    raise SystemExit(run(args.account, execute=args.execute))


if __name__ == "__main__":
    main()
