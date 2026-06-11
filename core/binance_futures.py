"""Binance USDT-M 永續合約：Testnet / 主網連線與下單。"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from core.binance_credentials import ExecMode, credentials_hint, load_credentials, mode_label

log = logging.getLogger(__name__)

try:
    from binance.um_futures import UMFutures

    HAS_FUTURES_CLIENT = True
except ImportError:
    HAS_FUTURES_CLIENT = False
    UMFutures = None  # type: ignore[misc, assignment]


@dataclass
class FuturesSettings:
    api_key: str = ""
    api_secret: str = ""
    testnet: bool = True
    leverage: int = 10
    total_capital: float = 1000.0
    position_pct: float = 1.0

    @classmethod
    def from_exec_mode(
        cls,
        mode: ExecMode | str,
        *,
        leverage: int = 10,
        total_capital: float | None = None,
        position_pct: float | None = None,
    ) -> "FuturesSettings":
        import os

        if isinstance(mode, str):
            mode = ExecMode(mode)
        api_key, api_secret = load_credentials(mode)
        return cls(
            api_key=api_key,
            api_secret=api_secret,
            testnet=mode == ExecMode.TESTNET,
            leverage=leverage,
            total_capital=float(
                total_capital if total_capital is not None else os.getenv("LIVE_TOTAL_CAPITAL", "1000")
            ),
            position_pct=float(
                position_pct if position_pct is not None else os.getenv("LIVE_POSITION_PCT", "1")
            ),
        )

    @classmethod
    def from_env(cls, testnet: bool = True, leverage: int = 10) -> "FuturesSettings":
        mode = ExecMode.TESTNET if testnet else ExecMode.LIVE
        return cls.from_exec_mode(mode, leverage=leverage)

    @property
    def exec_mode(self) -> ExecMode:
        return ExecMode.TESTNET if self.testnet else ExecMode.LIVE

    @property
    def margin_per_trade(self) -> float:
        return self.total_capital * self.position_pct / 100.0


def create_client(settings: FuturesSettings) -> "UMFutures":
    if not HAS_FUTURES_CLIENT:
        raise RuntimeError("請安裝 binance-futures-connector")
    base_url = "https://testnet.binancefuture.com" if settings.testnet else None
    return UMFutures(key=settings.api_key, secret=settings.api_secret, base_url=base_url)


def verify_connection(settings: FuturesSettings) -> bool:
    if not settings.api_key or not settings.api_secret:
        log.error(f"缺少 API 金鑰。{credentials_hint(settings.exec_mode)}")
        return False
    if not HAS_FUTURES_CLIENT:
        log.error("請安裝 binance-futures-connector: pip install binance-futures-connector")
        return False
    net = mode_label(settings.exec_mode)
    try:
        client = create_client(settings)
        acct = client.account()
        assets = acct.get("assets", [])
        usdt = next((a for a in assets if a.get("asset") == "USDT"), None)
        balance = float(usdt["walletBalance"]) if usdt else 0.0
        log.info(f"✅ 已連線 Binance 永續 {net}  可用 USDT: {balance:.2f}")
        return True
    except Exception as e:
        log.error(f"❌ Binance {net} 連線失敗: {e}")
        if settings.testnet:
            log.error("請確認金鑰來自 https://testnet.binancefuture.com（非主網）")
        return False


def _round_qty(qty: float, step: float) -> float:
    if step <= 0:
        return round(qty, 3)
    precision = int(round(-math.log10(step)))
    adjusted = math.floor(qty / step) * step
    return round(adjusted, precision)


def _get_lot_step(client: "UMFutures", symbol: str) -> float:
    info = client.exchange_info()
    sym_info = next(s for s in info["symbols"] if s["symbol"] == symbol)
    return float(next(f["stepSize"] for f in sym_info["filters"] if f["filterType"] == "LOT_SIZE"))


def resolve_leverage(client: "UMFutures", symbol: str, settings: FuturesSettings) -> int:
    if settings.leverage > 0:
        return settings.leverage
    try:
        brackets = client.leverage_brackets(symbol=symbol)
        if brackets:
            return max(int(b["initialLeverage"]) for b in brackets[0]["brackets"])
    except Exception as e:
        log.warning(f"取得 {symbol} 最大槓桿失敗: {e}")
    return 20


def calc_order_quantity(
    *,
    entry: float,
    position_size: float,
    strategy_id: str,
    settings: FuturesSettings,
    leverage: int,
) -> float:
    if strategy_id == "donchian" and position_size > 0:
        return position_size
    if entry <= 0:
        return 0.0
    margin = settings.margin_per_trade
    return margin * leverage / entry


def place_bracket_order(
    client: "UMFutures",
    *,
    symbol: str,
    side: str,
    quantity: float,
    stop: float,
    take_profit: float | None,
    leverage: int,
    strategy_id: str | None = None,
    client_order_id: str | None = None,
) -> dict:
    """市價進場 + 止損 + 可選止盈（reduceOnly）。回傳交易所市價單結果。"""
    from core.order_tags import build_client_order_id

    if not client_order_id and strategy_id:
        client_order_id = build_client_order_id(strategy_id, symbol)
    order_side = "BUY" if side == "long" else "SELL"
    exit_side = "SELL" if side == "long" else "BUY"

    try:
        client.change_leverage(symbol=symbol, leverage=leverage)
    except Exception as e:
        log.warning(f"{symbol} 設定槓桿失敗: {e}")

    step = _get_lot_step(client, symbol)
    qty = _round_qty(quantity, step)
    if qty <= 0:
        raise ValueError(f"{symbol} 計算數量為 0")

    entry_kwargs: dict = {"symbol": symbol, "side": order_side, "type": "MARKET", "quantity": qty}
    if client_order_id:
        entry_kwargs["newClientOrderId"] = client_order_id
    entry_result = client.new_order(**entry_kwargs)
    log.info(f"已下單 {order_side} {qty} {symbol} @ market")

    client.new_order(
        symbol=symbol,
        side=exit_side,
        type="STOP_MARKET",
        stopPrice=round(stop, 8),
        quantity=qty,
        reduceOnly=True,
    )
    log.info(f"止損 @ {stop:.6g}")

    if take_profit and take_profit > 0:
        client.new_order(
            symbol=symbol,
            side=exit_side,
            type="TAKE_PROFIT_MARKET",
            stopPrice=round(take_profit, 8),
            quantity=qty,
            reduceOnly=True,
        )
        log.info(f"止盈 @ {take_profit:.6g}")

    return entry_result
