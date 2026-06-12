"""Binance USDT-M 永續合約：Testnet / 主網連線與下單。"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

from core.binance_credentials import ExecMode, credentials_hint, load_credentials, mode_label

log = logging.getLogger(__name__)

try:
    from binance.error import ClientError
    from binance.um_futures import UMFutures

    HAS_FUTURES_CLIENT = True
except ImportError:
    HAS_FUTURES_CLIENT = False
    ClientError = Exception  # type: ignore[misc, assignment]
    UMFutures = None  # type: ignore[misc, assignment]

_TRADABLE_SYMBOLS: dict[str, frozenset[str]] = {}


@dataclass
class BracketOrderResult:
    """市價進場 + Algo 條件單（止損／止盈）結果。"""

    entry: dict
    stop_algo: dict | None = None
    take_profit_algo: dict | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        return self.stop_algo is not None and not self.errors


class BracketOrderError(Exception):
    """市價已成交但 bracket 未完整掛上。"""

    def __init__(self, message: str, result: BracketOrderResult):
        super().__init__(message)
        self.result = result


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


def format_binance_error(exc: Exception) -> str:
    if isinstance(exc, ClientError):
        code = getattr(exc, "error_code", None)
        msg = getattr(exc, "error_message", None) or ""
        if code is not None:
            return f"Binance API {code}: {msg}"
        return msg or type(exc).__name__
    text = str(exc).strip()
    return text or type(exc).__name__


def clear_tradable_symbols_cache() -> None:
    _TRADABLE_SYMBOLS.clear()


def get_tradable_symbols(client: "UMFutures", *, testnet: bool) -> frozenset[str]:
    key = "testnet" if testnet else "live"
    cached = _TRADABLE_SYMBOLS.get(key)
    if cached is not None:
        return cached
    info = client.exchange_info()
    symbols = {
        s["symbol"]
        for s in info.get("symbols", [])
        if s.get("status") == "TRADING" and s.get("contractType") == "PERPETUAL"
    }
    _TRADABLE_SYMBOLS[key] = frozenset(symbols)
    return _TRADABLE_SYMBOLS[key]


def ensure_tradable_symbol(client: "UMFutures", symbol: str, *, testnet: bool) -> None:
    sym = symbol.replace("/", "").upper()
    tradable = get_tradable_symbols(client, testnet=testnet)
    if sym not in tradable:
        net = "Testnet" if testnet else "主網"
        raise ValueError(f"{sym} 在 {net} 不可交易（Invalid symbol）")


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
        log.error(f"❌ Binance {net} 連線失敗: {format_binance_error(e)}")
        if settings.testnet:
            log.error("請確認金鑰來自 https://testnet.binancefuture.com（非主網）")
        return False


def _round_qty(qty: float, step: float) -> float:
    if step <= 0:
        return round(qty, 3)
    precision = int(round(-math.log10(step)))
    adjusted = math.floor(qty / step) * step
    return round(adjusted, precision)


def _round_price(price: float, tick: float) -> float:
    if tick <= 0:
        return round(price, 8)
    precision = max(0, int(round(-math.log10(tick))))
    adjusted = round(round(price / tick) * tick, precision)
    return adjusted


def _symbol_filters(client: "UMFutures", symbol: str) -> tuple[float, float]:
    info = client.exchange_info()
    sym_info = next(s for s in info["symbols"] if s["symbol"] == symbol)
    lot_step = float(next(f["stepSize"] for f in sym_info["filters"] if f["filterType"] == "LOT_SIZE"))
    price_tick = float(next(f["tickSize"] for f in sym_info["filters"] if f["filterType"] == "PRICE_FILTER"))
    return lot_step, price_tick


def _get_lot_step(client: "UMFutures", symbol: str) -> float:
    lot_step, _ = _symbol_filters(client, symbol)
    return lot_step


def resolve_leverage(client: "UMFutures", symbol: str, settings: FuturesSettings) -> int:
    if settings.leverage > 0:
        return settings.leverage
    try:
        brackets = client.leverage_brackets(symbol=symbol)
        if brackets:
            return max(int(b["initialLeverage"]) for b in brackets[0]["brackets"])
    except Exception as e:
        log.warning(f"取得 {symbol} 最大槓桿失敗: {format_binance_error(e)}")
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


def place_algo_conditional_order(
    client: "UMFutures",
    *,
    symbol: str,
    side: str,
    order_type: str,
    trigger_price: float,
    quantity: float,
    client_algo_id: str | None = None,
) -> dict:
    """
    下 Algo 條件單（STOP_MARKET / TAKE_PROFIT_MARKET）。
    POST /fapi/v1/algoOrder
    """
    lot_step, price_tick = _symbol_filters(client, symbol)
    qty = _round_qty(quantity, lot_step)
    trig = _round_price(trigger_price, price_tick)
    params: dict = {
        "algoType": "CONDITIONAL",
        "symbol": symbol,
        "side": side,
        "type": order_type,
        "triggerPrice": trig,
        "quantity": qty,
        "reduceOnly": "true",
        "workingType": "MARK_PRICE",
        "priceProtect": "true",
    }
    if client_algo_id:
        params["clientAlgoId"] = client_algo_id
    return client.sign_request("POST", "/fapi/v1/algoOrder", params)


def fetch_open_algo_orders(client: "UMFutures", symbol: str | None = None) -> list[dict]:
    """GET /fapi/v1/openAlgoOrders"""
    params: dict = {}
    if symbol:
        params["symbol"] = symbol
    try:
        rows = client.sign_request("GET", "/fapi/v1/openAlgoOrders", params)
        if isinstance(rows, list):
            return rows
    except Exception as e:
        log.warning(f"讀取 Algo 掛單失敗: {format_binance_error(e)}")
    return []


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
    testnet: bool = True,
) -> BracketOrderResult:
    """市價進場 + Algo 止損 + 可選 Algo 止盈。失敗時回傳部分結果並附 errors。"""
    from core.order_tags import build_client_order_id

    ensure_tradable_symbol(client, symbol, testnet=testnet)

    if not client_order_id and strategy_id:
        client_order_id = build_client_order_id(strategy_id, symbol)

    order_side = "BUY" if side == "long" else "SELL"
    exit_side = "SELL" if side == "long" else "BUY"
    result = BracketOrderResult(entry={})

    try:
        client.change_leverage(symbol=symbol, leverage=leverage)
    except Exception as e:
        log.warning(f"{symbol} 設定槓桿失敗: {format_binance_error(e)}")

    lot_step, price_tick = _symbol_filters(client, symbol)
    qty = _round_qty(quantity, lot_step)
    if qty <= 0:
        raise ValueError(f"{symbol} 計算數量為 0")

    stop_px = _round_price(stop, price_tick)
    tp_px = _round_price(take_profit, price_tick) if take_profit and take_profit > 0 else None

    entry_kwargs: dict = {"symbol": symbol, "side": order_side, "type": "MARKET", "quantity": qty}
    if client_order_id:
        entry_kwargs["newClientOrderId"] = client_order_id

    try:
        entry_result = client.new_order(**entry_kwargs)
        result.entry = entry_result
        log.info(f"已下單 {order_side} {qty} {symbol} @ market")
    except Exception as e:
        raise ValueError(f"{symbol} 市價進場失敗: {format_binance_error(e)}") from e

    try:
        result.stop_algo = place_algo_conditional_order(
            client,
            symbol=symbol,
            side=exit_side,
            order_type="STOP_MARKET",
            trigger_price=stop_px,
            quantity=qty,
        )
        log.info(f"止損 Algo @ {stop_px}")
    except Exception as e:
        msg = f"止損掛單失敗: {format_binance_error(e)}"
        result.errors.append(msg)
        log.error(f"{symbol} {msg}")

    if tp_px and tp_px > 0:
        try:
            result.take_profit_algo = place_algo_conditional_order(
                client,
                symbol=symbol,
                side=exit_side,
                order_type="TAKE_PROFIT_MARKET",
                trigger_price=tp_px,
                quantity=qty,
            )
            log.info(f"止盈 Algo @ {tp_px}")
        except Exception as e:
            msg = f"止盈掛單失敗: {format_binance_error(e)}"
            result.errors.append(msg)
            log.error(f"{symbol} {msg}")

    if result.errors:
        raise BracketOrderError("; ".join(result.errors), result)

    return result
