#!/usr/bin/env python3
"""
多策略 24/7 自動交易 — 多帳戶單進程輪詢

策略：EMA、唐奇安、Hunting Funding（預設全部啟用）

用法：
  python live_runner.py --verify-only --profiles all
  python live_runner.py --profiles all
  python live_runner.py --profiles account1:testnet,account2:testnet
  python live_runner.py --exec testnet          # 向後相容：僅 account1
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from core.env_bootstrap import load_project_env
from core.trade_data_store import maybe_clear_from_env

load_project_env()
_cleared = maybe_clear_from_env()

from core.account_profiles import (
    AccountProfile,
    load_profile,
    profile_configured,
    runner_profiles,
    state_file_for_profile,
)
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
class ProfileRunnerConfig:
    profile: AccountProfile
    strategy_ids: list[str] = field(default_factory=lambda: list(_ALL_STRATEGY_IDS))
    top_n: int = 100
    market: MarketType = "futures"
    kline_limit: int = 800
    futures: FuturesSettings | None = None

    def __post_init__(self) -> None:
        if self.futures is None:
            self.futures = FuturesSettings.from_profile(self.profile)


@dataclass
class RunnerConfig:
    profiles: list[ProfileRunnerConfig] = field(default_factory=list)
    scan_interval_sec: int = 30


def _load_state(profile: AccountProfile) -> dict:
    path = state_file_for_profile(profile)
    if not path.exists():
        return {"executed": {}}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _save_state(profile: AccountProfile, state: dict) -> None:
    path = state_file_for_profile(profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
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


def _filter_tradable_symbols(symbols: list[str], cfg: ProfileRunnerConfig) -> list[str]:
    if cfg.profile.network == ExecMode.PAPER:
        return symbols
    assert cfg.futures is not None
    client = create_client(cfg.futures)
    tradable = get_tradable_symbols(client, testnet=cfg.futures.testnet)
    filtered = [s for s in symbols if s.replace("/", "").upper() in tradable]
    skipped = len(symbols) - len(filtered)
    if skipped:
        log.info(
            f"[{cfg.profile.display_name}] 略過 {skipped} 個不可交易的 symbol"
        )
    return filtered


def _scan_round_for_profile(cfg: ProfileRunnerConfig) -> list[dict]:
    symbols = _filter_tradable_symbols(_resolve_symbols(cfg.top_n), cfg)
    if not symbols:
        log.warning(f"[{cfg.profile.display_name}] 榜單為空，略過本輪。")
        return []

    order_mode = OrderMode(cfg.profile.network.value)
    state = _load_state(cfg.profile)
    executed: dict[str, str] = state.setdefault("executed", {})
    placed: list[dict] = []
    assert cfg.futures is not None

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
                log.debug(f"[{cfg.profile.profile_id}] {bin_sym} {sid} K線失敗: {e}")
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
                account_id=cfg.profile.account_id,
                order_type="market",
                price=float(plan.entry),
                leverage=cfg.futures.leverage,
                take_profit=tp if tp and tp > 0 else None,
            )
            try:
                row = place_order(req, market=cfg.market)
                placed.append(row)
                executed[key] = row["created_at"]
                log.info(f"[{cfg.profile.display_name}] {sid} {bin_sym} {last.side}")
            except Exception as e:
                log.error(
                    f"[{cfg.profile.display_name}] {sid} {bin_sym} 下單失敗: "
                    f"{format_binance_error(e)}"
                )

    if len(executed) > 5000:
        executed = dict(list(executed.items())[-3000:])
    state["executed"] = executed
    _save_state(cfg.profile, state)
    return placed


def _scan_round(cfg: RunnerConfig) -> list[dict]:
    placed: list[dict] = []
    for pcfg in cfg.profiles:
        placed.extend(_scan_round_for_profile(pcfg))
    return placed


def run_loop(cfg: RunnerConfig) -> None:
    for pcfg in cfg.profiles:
        if pcfg.profile.network == ExecMode.PAPER:
            continue
        if not profile_configured(pcfg.profile):
            log.error(f"[{pcfg.profile.display_name}] 缺少 API 金鑰，略過此 profile。")
            continue
        assert pcfg.futures is not None
        if not verify_connection(pcfg.futures):
            raise SystemExit(1)

    names = ", ".join(get_strategy(s).name for s in cfg.profiles[0].strategy_ids)
    profile_names = ", ".join(p.profile.display_name for p in cfg.profiles)
    log.info(
        f"🚀 多帳戶自動交易啟動  Profiles: {profile_names}  "
        f"每 {cfg.scan_interval_sec}s 一輪"
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


def _resolve_profiles(args: argparse.Namespace) -> list[AccountProfile]:
    if args.profiles:
        if args.profiles.lower() == "all":
            return runner_profiles(include_live=args.confirm_live)
        if args.profiles.lower() == "legacy":
            return [load_profile("account1", args.exec)]
        prev = os.environ.get("RUNNER_PROFILES")
        os.environ["RUNNER_PROFILES"] = args.profiles
        try:
            return runner_profiles(include_live=args.confirm_live)
        finally:
            if prev is None:
                os.environ.pop("RUNNER_PROFILES", None)
            else:
                os.environ["RUNNER_PROFILES"] = prev

    if os.getenv("RUNNER_PROFILES"):
        return runner_profiles(include_live=args.confirm_live)

    return [load_profile("account1", args.exec)]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="多策略 24/7 自動交易（多帳戶）")
    p.add_argument("--strategies", default="all", help="策略 ID，逗號分隔，或 all")
    p.add_argument("--top-n", type=int, default=100)
    p.add_argument(
        "--profiles",
        default="",
        help="帳戶 profile，逗號分隔 account_id:network，或 all（全部已設定帳戶）",
    )
    p.add_argument(
        "--exec",
        choices=["paper", "testnet", "live"],
        default="testnet",
        help="向後相容：未指定 --profiles 時僅跑 account1 此模式",
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
        help="profile 含 live 時必須加上此旗標",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    _setup_logging()
    if _cleared:
        log.info("CLEAR_TRADE_DATA：已清空本地訂單／state → %s", ", ".join(_cleared))

    profiles = _resolve_profiles(args)
    has_live = any(p.network == ExecMode.LIVE for p in profiles)
    if has_live and not args.confirm_live and not args.verify_only:
        raise SystemExit("profile 含 live 請加上 --confirm-live 以確認主網實盤風險")

    if args.strategies.strip().lower() == "all":
        strategy_ids = list(_ALL_STRATEGY_IDS)
    else:
        strategy_ids = [s.strip() for s in args.strategies.split(",") if s.strip()]
        unknown = [s for s in strategy_ids if s not in STRATEGIES]
        if unknown:
            raise SystemExit(f"未知策略: {unknown}，可選: {_ALL_STRATEGY_IDS}")

    profile_cfgs: list[ProfileRunnerConfig] = []
    for profile in profiles:
        profile_cfgs.append(
            ProfileRunnerConfig(
                profile=profile,
                strategy_ids=strategy_ids,
                top_n=args.top_n,
                kline_limit=args.kline_limit,
                futures=FuturesSettings.from_profile(
                    profile,
                    leverage=args.leverage,
                    total_capital=args.total_capital,
                    position_pct=args.position_pct,
                ),
            )
        )

    if args.verify_only:
        ok = True
        for pcfg in profile_cfgs:
            p = pcfg.profile
            if p.network == ExecMode.PAPER:
                log.info(f"✅ {p.display_name} — paper 不需 API")
                continue
            if not profile_configured(p):
                log.error(f"❌ {p.display_name} — 缺少金鑰")
                ok = False
                continue
            assert pcfg.futures is not None
            if verify_connection(pcfg.futures):
                log.info(f"✅ {p.display_name}")
            else:
                ok = False
        raise SystemExit(0 if ok else 1)

    if not profile_cfgs:
        raise SystemExit("沒有可執行的 profile；請設定 RUNNER_PROFILES 或 --profiles")

    cfg = RunnerConfig(profiles=profile_cfgs, scan_interval_sec=args.scan_interval)
    run_loop(cfg)


if __name__ == "__main__":
    main()
