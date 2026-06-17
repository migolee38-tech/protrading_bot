#!/usr/bin/env python3
"""
OKX Demo 條件單流程測試（Step 2）。

預設 dry-run；加 --execute 才會在 Demo 下單。

流程：市價小倉 → 只掛 SL → 只掛 TP → amend SL → 取消 TP → 平倉
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.env_bootstrap import load_project_env

load_project_env()
os.environ.setdefault("EXCHANGE", "okx")

from core.account_profiles import load_profile  # noqa: E402
from core.binance_credentials import ExecMode  # noqa: E402
from core.exchange_config import is_okx  # noqa: E402
from core.okx_futures import (  # noqa: E402
    OkxClients,
    OkxFuturesSettings,
    amend_stop_algo,
    cancel_algo_order,
    create_clients,
    exchange_position_qty,
    fetch_open_algo_orders,
    format_okx_error,
    get_mark_price,
    place_market_entry,
    place_market_reduce,
    place_stop_algo,
    place_tp_algo,
    set_leverage,
    to_inst_id,
)

log = logging.getLogger("test_okx_bracket")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
    )


def _sleep(sec: float = 1.0) -> None:
    time.sleep(sec)


def run_test(
    clients: OkxClients,
    *,
    symbol: str,
    side: str,
    coin_qty: float,
    execute: bool,
) -> int:
    inst_id = to_inst_id(symbol)
    mark = get_mark_price(clients.public, symbol)
    if mark <= 0:
        log.error(f"無法取得 {inst_id} 標記價")
        return 1

    if side == "long":
        stop_px = mark * 0.97
        tp_px = mark * 1.03
    else:
        stop_px = mark * 1.03
        tp_px = mark * 0.97

    log.info(f"標的 {inst_id}  方向 {side}  標記價 {mark:.4f}")
    log.info(f"測試數量 {coin_qty} 幣  SL={stop_px:.4f}  TP={tp_px:.4f}")

    if not execute:
        log.info("dry-run：加 --execute 才會在 Demo 實際下單")
        return 0

    stop_algo_id: str | None = None
    tp_algo_id: str | None = None

    try:
        set_leverage(clients.account, symbol, clients.settings.leverage)
        log.info("1/6 市價開倉 …")
        entry = place_market_entry(
            clients, symbol=symbol, side=side, quantity=coin_qty
        )
        log.info(f"   ordId={entry.get('ordId')}")
        _sleep()

        qty = exchange_position_qty(clients, symbol, side)
        log.info(f"   持倉 {qty} 幣")
        if qty <= 0:
            log.error("開倉後無持倉")
            return 1

        log.info("2/6 只掛止損 …")
        stop_row = place_stop_algo(
            clients,
            symbol=symbol,
            side=side,
            trigger_price=stop_px,
            quantity=qty,
        )
        stop_algo_id = str(stop_row.get("algoId", ""))
        _sleep()

        log.info("3/6 只掛止盈 …")
        tp_row = place_tp_algo(
            clients,
            symbol=symbol,
            side=side,
            trigger_price=tp_px,
            quantity=qty,
        )
        tp_algo_id = str(tp_row.get("algoId", ""))
        _sleep()

        open_algos = fetch_open_algo_orders(clients.trade, symbol)
        log.info(f"   未觸發 algo 數: {len(open_algos)}")

        new_stop = stop_px * (1.001 if side == "long" else 0.999)
        log.info(f"4/6 amend 止損 → {new_stop:.4f} …")
        amend_stop_algo(
            clients,
            stop_algo_id,
            symbol=symbol,
            new_stop=new_stop,
            quantity=qty,
        )
        _sleep()

        if tp_algo_id:
            log.info("5/6 取消止盈 …")
            cancel_algo_order(clients.trade, tp_algo_id, symbol=symbol)
            _sleep()

        log.info("6/6 市價平倉 …")
        qty = exchange_position_qty(clients, symbol, side)
        if qty > 0:
            place_market_reduce(clients, symbol=symbol, side=side, quantity=qty)
        _sleep()

        if stop_algo_id:
            cancel_algo_order(clients.trade, stop_algo_id, symbol=symbol)

        remaining = exchange_position_qty(clients, symbol, side)
        log.info(f"✅ 測試完成  剩餘持倉 {remaining} 幣")
        return 0 if remaining <= 0 else 1

    except Exception as e:
        log.error(f"❌ 測試失敗: {format_okx_error(e)}")
        try:
            qty = exchange_position_qty(clients, symbol, side)
            if qty > 0:
                log.warning("嘗試緊急平倉 …")
                place_market_reduce(clients, symbol=symbol, side=side, quantity=qty)
            if stop_algo_id:
                cancel_algo_order(clients.trade, stop_algo_id, symbol=symbol)
            if tp_algo_id:
                cancel_algo_order(clients.trade, tp_algo_id, symbol=symbol)
        except Exception as cleanup_exc:
            log.error(f"清理失敗: {format_okx_error(cleanup_exc)}")
        return 1


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="OKX Demo 條件單測試（Step 2）")
    p.add_argument("--account", default="account1", help="account1 / account2")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--side", choices=["long", "short"], default="long")
    p.add_argument(
        "--coin-qty",
        type=float,
        default=0.0,
        help="開倉幣數（預設 BTC=0.0002, 其他=最小可下）",
    )
    p.add_argument(
        "--execute",
        action="store_true",
        help="實際在 Demo 下單（預設 dry-run）",
    )
    p.add_argument("--verbose", action="store_true")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    _setup_logging(args.verbose)

    if not is_okx():
        log.warning("建議設定 EXCHANGE=okx")

    profile = load_profile(args.account, ExecMode.TESTNET)
    settings = OkxFuturesSettings.from_profile(profile)
    clients = create_clients(settings)

    coin_qty = args.coin_qty
    if coin_qty <= 0:
        coin_qty = 0.0002 if args.symbol.upper().startswith("BTC") else 0.01

    if args.execute and not settings.testnet:
        log.error("--execute 僅允許 Demo（testnet）；請使用 testnet API 金鑰")
        raise SystemExit(2)

    if args.execute:
        log.warning("將在 OKX Demo 實際下單；約 1 分鐘內完成開倉/掛單/平倉")

    code = run_test(
        clients,
        symbol=args.symbol,
        side=args.side,
        coin_qty=coin_qty,
        execute=args.execute,
    )
    raise SystemExit(code)


if __name__ == "__main__":
    main()
