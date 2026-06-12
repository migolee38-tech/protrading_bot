#!/usr/bin/env python3
"""
多策略 24/7 自動交易 — paper / testnet / live

策略：EMA、唐奇安、RSI、MACD、Hunting Funding（預設全部啟用）

用法：
  python live_runner.py --verify-only --exec testnet
  python live_runner.py --exec paper
  python live_runner.py --exec testnet
  python live_runner.py --exec live
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

from core.binance_credentials import ExecMode, mode_label
from core.binance_futures import (
    FuturesSettings,
    create_client,
    format_binance_error,
    get_tradable_symbols,
    verify_connection,
)
from core.market_data import MarketType, fetch_klines
from core.order_executor import OrderMode, OrderRequest, place_order
from core.strategy_registry import STRATEGIES, get_strategy, scan_signals_for, with_symbol
from core.universe import top_usdt_pairs_by_volume

_LOG_FILE = Path(__file__).resolve().parent / "logs" / "live_runner.log"
_STATE_FILE = Path(__file__).resolve().parent / "data" / "live_trader_state.json"
_ALL_STRATEGY_IDS = list(STRATEGIES.keys())

log = logging.getLogger("LiveRunner")


def _setup_logging() -> None:
    _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(_LOG_FILE, encoding="utf-8"),
        ],
        force=True,
    )


@dataclass
class RunnerConfig:
    strategy_ids: list[str] = field(default_factory=lambda: list(_ALL_STRATEGY_IDS))
    top_n: int = 100
    market: MarketType = "futures"
    kline_limit: int = 800
    scan_interval_sec: int = 30
    exec_mode: ExecMode = ExecMode.TESTNET
    futures: FuturesSettings = field(
        default_factory=lambda: FuturesSettings.from_exec_mode(ExecMode.TESTNET)
    )


def _load_state() -> dict:
    if not _STATE_FILE.exists():
        return {"executed": {}}
    with open(_STATE_FILE, encoding="utf-8") as f:
        return json.load(f)


def _save_state(state: dict) -> None:
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _signal_key(strategy_id: str, symbol: str, bar_index: int) -> str:
    return f"{strategy_id}:{symbol}:{bar_index}"


def _is_fresh_signal(bar_index: int, df_len: int) -> bool:
    return bar_index >= df_len - 2


def _resolve_symbols(top_n: int) -> list[str]:
    df = top_usdt_pairs_by_volume(top_n=top_n, market="futures")
    if df.empty:
        return []
    return df["symbol"].tolist()


def _filter_tradable_symbols(symbols: list[str], cfg: RunnerConfig) -> list[str]:
    if cfg.exec_mode == ExecMode.PAPER:
        return symbols
    client = create_client(cfg.futures)
    tradable = get_tradable_symbols(client, testnet=cfg.futures.testnet)
    filtered = [s for s in symbols if s.replace("/", "").upper() in tradable]
    skipped = len(symbols) - len(filtered)
    if skipped:
        log.info(
            f"略過 {skipped} 個在 {mode_label(cfg.exec_mode)} 不可交易的 symbol"
        )
    return filtered


def _scan_round(cfg: RunnerConfig) -> list[dict]:
    symbols = _filter_tradable_symbols(_resolve_symbols(cfg.top_n), cfg)
    if not symbols:
        log.warning("成交量榜單為空或無可交易 symbol，略過本輪。")
        return []

    order_mode = OrderMode(cfg.exec_mode.value)
    state = _load_state()
    executed: dict[str, str] = state.setdefault("executed", {})
    placed: list[dict] = []

    for sym in symbols:
        bin_sym = sym.replace("/", "").upper()
        for sid in cfg.strategy_ids:
            meta = get_strategy(sid)
            try:
                raw = fetch_klines(
                    bin_sym,
                    interval=meta.timeframe,
                    limit=cfg.kline_limit,
                    market=cfg.market,
                )
            except Exception as e:
                log.debug(f"{bin_sym} {sid} K線失敗: {e}")
                continue

            prep = meta.prepare_df(with_symbol(raw, bin_sym, kline_limit=cfg.kline_limit))
            signals = scan_signals_for(sid, prep)
            if not signals:
                continue

            last = signals[-1]
            if not _is_fresh_signal(last.bar_index, len(prep)):
                continue

            key = _signal_key(sid, bin_sym, last.bar_index)
            if key in executed:
                continue

            plan = last.plan
            tp = getattr(plan, "tp_final", None)
            pos_size = float(getattr(plan, "position_size", 0) or 0)
            if order_mode == OrderMode.PAPER:
                qty = pos_size if sid == "donchian" and pos_size > 0 else max(pos_size, 0.001)
            else:
                qty = pos_size if sid == "donchian" and pos_size > 0 else 0.0

            req = OrderRequest(
                symbol=bin_sym,
                strategy_id=sid,
                side=last.side,
                entry=float(plan.entry),
                stop=float(plan.stop),
                quantity=qty,
                mode=order_mode,
                order_type="market",
                price=float(plan.entry),
                leverage=cfg.futures.leverage,
                take_profit=tp if tp and tp > 0 else None,
            )
            try:
                row = place_order(req, market=cfg.market)
                placed.append(row)
                executed[key] = row["created_at"]
                log.info(f"[{order_mode.value}] {sid} {bin_sym} {last.side}")
            except Exception as e:
                log.error(
                    f"[{order_mode.value}] {sid} {bin_sym} 下單失敗: {format_binance_error(e)}"
                )

    if len(executed) > 5000:
        executed = dict(list(executed.items())[-3000:])
    state["executed"] = executed
    _save_state(state)
    return placed


def run_loop(cfg: RunnerConfig) -> None:
    if cfg.exec_mode != ExecMode.PAPER and not verify_connection(cfg.futures):
        raise SystemExit(1)

    names = ", ".join(get_strategy(s).name for s in cfg.strategy_ids)
    log.info(
        f"🚀 多策略自動交易啟動  [{mode_label(cfg.exec_mode)}]  "
        f"Top {cfg.top_n} · 每 {cfg.scan_interval_sec}s 一輪"
    )
    log.info(f"策略：{names}")

    while True:
        try:
            placed = _scan_round(cfg)
            if placed:
                log.info(f"本輪成交 {len(placed)} 筆")
        except KeyboardInterrupt:
            log.info("已停止。")
            break
        except Exception as e:
            log.error(f"掃描錯誤: {e}")
        time.sleep(cfg.scan_interval_sec)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="多策略 24/7 自動交易")
    p.add_argument("--strategies", default="all", help="策略 ID，逗號分隔，或 all")
    p.add_argument("--top-n", type=int, default=100)
    p.add_argument(
        "--exec",
        choices=["paper", "testnet", "live"],
        default="testnet",
        help="paper=本地模擬  testnet=模擬倉  live=主網實盤",
    )
    p.add_argument("--scan-interval", type=int, default=30)
    p.add_argument("--kline-limit", type=int, default=800)
    p.add_argument("--leverage", type=int, default=10)
    p.add_argument("--total-capital", type=float, default=1000.0)
    p.add_argument("--position-pct", type=float, default=1.0)
    p.add_argument("--verify-only", action="store_true")
    p.add_argument(
        "--confirm-live",
        action="store_true",
        help="live 模式必須加上此旗標以確認主網實盤風險",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    _setup_logging()

    exec_mode = ExecMode(args.exec)
    if exec_mode == ExecMode.LIVE and not args.confirm_live and not args.verify_only:
        raise SystemExit("live 模式請加上 --confirm-live 以確認主網實盤風險")

    if args.strategies.strip().lower() == "all":
        strategy_ids = list(_ALL_STRATEGY_IDS)
    else:
        strategy_ids = [s.strip() for s in args.strategies.split(",") if s.strip()]
        unknown = [s for s in strategy_ids if s not in STRATEGIES]
        if unknown:
            raise SystemExit(f"未知策略: {unknown}，可選: {_ALL_STRATEGY_IDS}")

    futures = FuturesSettings.from_exec_mode(
        exec_mode,
        leverage=args.leverage,
        total_capital=args.total_capital,
        position_pct=args.position_pct,
    )

    if args.verify_only:
        if exec_mode == ExecMode.PAPER:
            log.info("paper 模式不需 API，設定正確。")
            raise SystemExit(0)
        ok = verify_connection(futures)
        raise SystemExit(0 if ok else 1)

    cfg = RunnerConfig(
        strategy_ids=strategy_ids,
        top_n=args.top_n,
        kline_limit=args.kline_limit,
        scan_interval_sec=args.scan_interval,
        exec_mode=exec_mode,
        futures=futures,
    )
    run_loop(cfg)


if __name__ == "__main__":
    main()
