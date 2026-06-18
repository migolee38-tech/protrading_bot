"""OKX USDT 永續（SWAP）：Demo / 主網連線、下單與條件單。"""

from __future__ import annotations

import logging
import math
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Literal, TypeVar

from core.account_profiles import AccountProfile
from core.binance_credentials import ExecMode, mode_label
from core.okx_credentials import credentials_for_profile, credentials_hint_for_profile

log = logging.getLogger(__name__)

try:
    import okx.Account as OkxAccount
    import okx.PublicData as OkxPublic
    import okx.Trade as OkxTrade

    HAS_OKX_CLIENT = True
except ImportError:
    HAS_OKX_CLIENT = False
    OkxAccount = None  # type: ignore[misc, assignment]
    OkxPublic = None  # type: ignore[misc, assignment]
    OkxTrade = None  # type: ignore[misc, assignment]

_INST_ID_RE = re.compile(r"^[A-Z0-9]+-USDT-SWAP$")
_UNIFIED_SYMBOL_RE = re.compile(r"^[A-Z0-9]+USDT$")
_INSTRUMENT_CACHE: dict[str, dict[str, Any]] = {}
_POS_MODE_CACHE: dict[str, str] = {}
_LEVERAGE_SET_CACHE: set[str] = set()
_LAST_OKX_WRITE_TS = 0.0
_OKX_WRITE_MIN_INTERVAL_SEC = 0.6
_TRANSIENT_OKX_CODES = frozenset({"50001", "50011", "50013", "50026"})
_OKX_ERROR_CODE_RE = re.compile(r"OKX API (\d+):")
T = TypeVar("T")
TriggerPxType = Literal["last", "mark", "index"]
TD_MODE = "cross"


@dataclass
class OkxFuturesSettings:
    api_key: str = ""
    api_secret: str = ""
    passphrase: str = ""
    testnet: bool = True
    leverage: int = 10
    total_capital: float = 1000.0
    position_pct: float = 1.0
    account_id: str = "account1"

    @classmethod
    def from_profile(
        cls,
        profile: AccountProfile,
        *,
        leverage: int = 10,
        total_capital: float | None = None,
        position_pct: float | None = None,
    ) -> "OkxFuturesSettings":
        from core.account_profiles import profile_capital, profile_position_pct

        api_key, api_secret, passphrase = credentials_for_profile(profile)
        return cls(
            api_key=api_key,
            api_secret=api_secret,
            passphrase=passphrase,
            testnet=profile.network == ExecMode.TESTNET,
            account_id=profile.account_id,
            leverage=leverage,
            total_capital=float(
                total_capital if total_capital is not None else profile_capital(profile)
            ),
            position_pct=float(
                position_pct if position_pct is not None else profile_position_pct(profile)
            ),
        )

    @classmethod
    def from_exec_mode(
        cls,
        mode: ExecMode | str,
        *,
        account_id: str = "account1",
        leverage: int = 10,
        total_capital: float | None = None,
        position_pct: float | None = None,
    ) -> "OkxFuturesSettings":
        from core.account_profiles import load_profile

        if isinstance(mode, str):
            mode = ExecMode(mode)
        return cls.from_profile(
            load_profile(account_id, mode),
            leverage=leverage,
            total_capital=total_capital,
            position_pct=position_pct,
        )

    @property
    def exec_mode(self) -> ExecMode:
        return ExecMode.TESTNET if self.testnet else ExecMode.LIVE

    @property
    def demo_flag(self) -> str:
        """OKX SDK：1 = Demo Trading，0 = 主網。"""
        return "1" if self.testnet else "0"

    @property
    def margin_per_trade(self) -> float:
        return self.total_capital * self.position_pct / 100.0


@dataclass
class OkxClients:
    """Account / Trade / Public API 共用同一組金鑰與 demo flag。"""

    account: Any
    trade: Any
    public: Any
    settings: OkxFuturesSettings


def to_inst_id(symbol: str) -> str:
    """
    統一 symbol → OKX instId。
    BTCUSDT / BTC/USDT → BTC-USDT-SWAP
    """
    raw = symbol.strip().upper().replace("/", "")
    if _INST_ID_RE.match(raw):
        return raw
    if raw.endswith("-SWAP"):
        return raw
    if _UNIFIED_SYMBOL_RE.match(raw):
        base = raw[:-4]
        return f"{base}-USDT-SWAP"
    if "-" in raw:
        return raw if raw.endswith("-SWAP") else f"{raw}-SWAP"
    raise ValueError(f"無法轉換為 OKX instId: {symbol!r}")


def from_inst_id(inst_id: str) -> str:
    """OKX instId → 專案內統一 symbol（BTCUSDT）。"""
    inst = inst_id.strip().upper()
    if inst.endswith("-USDT-SWAP"):
        return inst.replace("-USDT-SWAP", "USDT")
    if inst.endswith("-SWAP"):
        parts = inst.split("-")
        if len(parts) >= 2:
            return f"{parts[0]}{parts[1]}"
    return inst.replace("-", "").replace("SWAP", "")


def _api_args(settings: OkxFuturesSettings) -> tuple[str, str, str, bool, str]:
    return (
        settings.api_key,
        settings.api_secret,
        settings.passphrase,
        False,
        settings.demo_flag,
    )


def create_clients(settings: OkxFuturesSettings) -> OkxClients:
    if not HAS_OKX_CLIENT:
        raise RuntimeError("請安裝 python-okx: pip install python-okx")
    if not settings.api_key or not settings.api_secret or not settings.passphrase:
        raise ValueError("缺少 OKX API 金鑰或 passphrase")
    args = _api_args(settings)
    return OkxClients(
        account=OkxAccount.AccountAPI(*args),
        trade=OkxTrade.TradeAPI(*args),
        public=OkxPublic.PublicAPI(*args),
        settings=settings,
    )


def create_client(settings: OkxFuturesSettings) -> Any:
    """向後相容 Step 1：回傳 Account API。"""
    return create_clients(settings).account


def create_trade_client(settings: OkxFuturesSettings) -> Any:
    return create_clients(settings).trade


def create_public_client(settings: OkxFuturesSettings) -> Any:
    return create_clients(settings).public


def _api_code(response: dict[str, Any]) -> str:
    return str(response.get("code", ""))


def _api_msg(response: dict[str, Any]) -> str:
    return str(response.get("msg", "") or "")


def ensure_ok_response(response: dict[str, Any], *, context: str = "") -> dict[str, Any]:
    code = _api_code(response)
    if code != "0":
        prefix = f"{context}: " if context else ""
        raise RuntimeError(f"{prefix}OKX API {code}: {_api_msg(response)}")
    return response


def format_okx_error(exc: Exception) -> str:
    text = str(exc).strip()
    return text or type(exc).__name__


def okx_error_code(exc: Exception) -> str | None:
    match = _OKX_ERROR_CODE_RE.search(str(exc))
    return match.group(1) if match else None


def is_transient_okx_error(exc: Exception) -> bool:
    """50001 / 503 等暫時性錯誤，適合退避重試。"""
    code = okx_error_code(exc)
    if code in _TRANSIENT_OKX_CODES:
        return True
    msg = str(exc).lower()
    return (
        "503" in msg
        or "temporarily unavailable" in msg
        or "too many requests" in msg
        or "rate limit" in msg
    )


def _throttle_okx_write() -> None:
    global _LAST_OKX_WRITE_TS
    now = time.monotonic()
    wait = _OKX_WRITE_MIN_INTERVAL_SEC - (now - _LAST_OKX_WRITE_TS)
    if wait > 0:
        time.sleep(wait)
    _LAST_OKX_WRITE_TS = time.monotonic()


def call_okx_with_retry(
    fn: Callable[[], T],
    *,
    context: str = "",
    max_attempts: int = 4,
    base_delay_sec: float = 0.6,
) -> T:
    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        try:
            return fn()
        except Exception as e:
            last_exc = e
            if not is_transient_okx_error(e) or attempt >= max_attempts - 1:
                raise
            delay = base_delay_sec * (2**attempt)
            label = context or "OKX 請求"
            log.warning(
                f"{label} 暫時失敗，{delay:.1f}s 後重試 "
                f"({attempt + 1}/{max_attempts}): {format_okx_error(e)}"
            )
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc


def instrument_max_leverage(instrument: dict[str, Any]) -> int:
    for key in ("lever", "maxLever", "maxLmt"):
        raw = instrument.get(key)
        if raw is None or not str(raw).strip():
            continue
        try:
            return max(1, int(float(raw)))
        except (TypeError, ValueError):
            continue
    return 125


def resolve_symbol_leverage(public: Any, symbol: str, requested: int) -> int:
    """依合約上限裁切槓桿（避免 59102）。"""
    req = max(1, int(requested or 1))
    instrument = get_instrument(public, symbol)
    return min(req, instrument_max_leverage(instrument))


def _float_val(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _round_to_step(value: float, step: float) -> float:
    if step <= 0:
        return value
    precision = max(0, int(round(-math.log10(step))))
    adjusted = math.floor(value / step + 1e-12) * step
    return round(adjusted, precision)


def _fmt_num(value: float) -> str:
    text = f"{value:.12f}".rstrip("0").rstrip(".")
    return text or "0"


def get_instrument(public: Any, symbol: str, *, refresh: bool = False) -> dict[str, Any]:
    inst_id = to_inst_id(symbol)
    if not refresh and inst_id in _INSTRUMENT_CACHE:
        return _INSTRUMENT_CACHE[inst_id]
    response = ensure_ok_response(
        public.get_instruments("SWAP", instId=inst_id),
        context=f"查詢合約規格 {inst_id}",
    )
    rows = response.get("data") or []
    if not rows:
        raise ValueError(f"找不到合約規格: {inst_id}")
    row = rows[0]
    if str(row.get("state", "")).lower() not in ("live", "suspend"):
        log.warning(f"{inst_id} state={row.get('state')}")
    _INSTRUMENT_CACHE[inst_id] = row
    return row


def clear_instrument_cache() -> None:
    _INSTRUMENT_CACHE.clear()


def _cache_key_for_settings(settings: OkxFuturesSettings) -> str:
    return f"{settings.account_id}:{settings.demo_flag}"


def get_pos_mode(account: Any, settings: OkxFuturesSettings | None = None) -> str:
    """net_mode 或 long_short_mode。"""
    key = _cache_key_for_settings(settings) if settings else "default"
    cached = _POS_MODE_CACHE.get(key)
    if cached:
        return cached
    response = ensure_ok_response(account.get_account_config(), context="查詢帳戶模式")
    rows = response.get("data") or []
    mode = str(rows[0].get("posMode", "net_mode")) if rows else "net_mode"
    if settings:
        _POS_MODE_CACHE[key] = mode
    return mode


def is_hedge_mode(pos_mode: str) -> bool:
    return pos_mode == "long_short_mode"


def _order_pos_side(position_side: str, pos_mode: str) -> str:
    """long_short_mode 需帶 posSide；net_mode 用 net。"""
    if is_hedge_mode(pos_mode):
        return position_side.lower()
    return "net"


def _position_row_qty(row: dict[str, Any], side: str, pos_mode: str) -> float:
    pos = _float_val(row.get("pos"))
    if pos <= 0:
        return 0.0
    if is_hedge_mode(pos_mode):
        row_side = str(row.get("posSide", "")).lower()
        if row_side == side.lower():
            return pos
        return 0.0
    if side == "long" and pos > 0:
        return pos
    if side == "short" and pos < 0:
        return abs(pos)
    return 0.0


def coin_qty_to_contracts(coin_qty: float, instrument: dict[str, Any]) -> float:
    ct_val = _float_val(instrument.get("ctVal"))
    if ct_val <= 0:
        raise ValueError("合約 ctVal 無效")
    lot_sz = _float_val(instrument.get("lotSz"), 0.01)
    contracts = coin_qty / ct_val
    contracts = _round_to_step(contracts, lot_sz)
    min_sz = _float_val(instrument.get("minSz"), lot_sz)
    if contracts < min_sz:
        raise ValueError(
            f"數量過小：{coin_qty} 幣 → {contracts} 張，最小 {min_sz} 張"
        )
    return contracts


def contracts_to_coin(contracts: float, instrument: dict[str, Any]) -> float:
    ct_val = _float_val(instrument.get("ctVal"))
    return contracts * ct_val


def _round_price(price: float, instrument: dict[str, Any]) -> float:
    tick = _float_val(instrument.get("tickSz"), 0.1)
    return _round_to_step(price, tick)


def _exit_side(position_side: str) -> str:
    return "sell" if position_side == "long" else "buy"


def _entry_side(position_side: str) -> str:
    return "buy" if position_side == "long" else "sell"


def _algo_row(response: dict[str, Any], *, context: str) -> dict[str, Any]:
    ensure_ok_response(response, context=context)
    rows = response.get("data") or []
    if not rows:
        raise RuntimeError(f"{context}: 回傳為空")
    row = rows[0]
    if str(row.get("sCode", "0")) not in ("0", ""):
        raise RuntimeError(
            f"{context}: OKX sCode {row.get('sCode')}: {row.get('sMsg', '')}"
        )
    return row


def fetch_usdt_balance(client: Any) -> tuple[float, float]:
    """回傳 (總權益 USDT, 可用 USDT)。"""
    response = ensure_ok_response(
        client.get_account_balance(ccy="USDT"),
        context="查詢餘額",
    )
    data = response.get("data") or []
    if not data:
        return 0.0, 0.0
    details = data[0].get("details") or []
    usdt = next((row for row in details if row.get("ccy") == "USDT"), None)
    if usdt:
        equity = _float_val(usdt.get("eq") or usdt.get("cashBal"))
        available = _float_val(usdt.get("availBal") or usdt.get("availEq"))
        return equity, available
    total_eq = _float_val(data[0].get("totalEq"))
    return total_eq, total_eq


def fetch_swap_positions(client: Any, *, inst_id: str | None = None) -> list[dict[str, Any]]:
    """查詢 SWAP 持倉；pos 為合約張數（net 模式正=多、負=空）。"""
    response = ensure_ok_response(
        client.get_positions(
            instType="SWAP",
            instId=to_inst_id(inst_id) if inst_id else "",
        ),
        context="查詢持倉",
    )
    rows = response.get("data") or []
    out: list[dict[str, Any]] = []
    for row in rows:
        pos = _float_val(row.get("pos"))
        if abs(pos) <= 0:
            continue
        out.append(row)
    return out


def get_mark_price(public: Any, symbol: str) -> float:
    inst_id = to_inst_id(symbol)
    response = ensure_ok_response(
        public.get_mark_price("SWAP", instId=inst_id),
        context="查詢標記價",
    )
    rows = response.get("data") or []
    if not rows:
        return 0.0
    return _float_val(rows[0].get("markPx"))


def exchange_position_qty(
    clients: OkxClients,
    symbol: str,
    side: str,
) -> float:
    """回傳該方向持倉數量（幣），無倉為 0。"""
    inst_id = to_inst_id(symbol)
    try:
        rows = fetch_swap_positions(clients.account, inst_id=inst_id)
    except Exception:
        return 0.0
    try:
        instrument = get_instrument(clients.public, inst_id)
    except Exception:
        instrument = {"ctVal": "1"}
    pos_mode = get_pos_mode(clients.account, clients.settings)
    for row in rows:
        contracts = _position_row_qty(row, side, pos_mode)
        if contracts > 0:
            return contracts_to_coin(contracts, instrument)
    return 0.0


def set_leverage(
    account: Any,
    symbol: str,
    leverage: int,
    *,
    td_mode: str = TD_MODE,
    settings: OkxFuturesSettings | None = None,
    public: Any | None = None,
) -> int:
    """設定槓桿；已設定過則跳過 API。回傳實際使用的槓桿。"""
    inst_id = to_inst_id(symbol)
    effective = leverage
    if public is not None:
        effective = resolve_symbol_leverage(public, symbol, leverage)

    cache_suffix = _cache_key_for_settings(settings) if settings else "default"
    cache_id = f"{cache_suffix}:{inst_id}:{effective}"
    if cache_id in _LEVERAGE_SET_CACHE:
        return effective

    def _do() -> None:
        _throttle_okx_write()
        ensure_ok_response(
            account.set_leverage(str(effective), td_mode, instId=inst_id),
            context=f"設定槓桿 {inst_id}",
        )

    try:
        call_okx_with_retry(_do, context=f"設定槓桿 {inst_id}", max_attempts=3)
        _LEVERAGE_SET_CACHE.add(cache_id)
        if effective != leverage:
            log.info(f"{inst_id} 槓桿 {leverage}x → {effective}x（合約上限）")
    except Exception as e:
        log.warning(f"{inst_id} 設定槓桿失敗: {format_okx_error(e)}")
    return effective


def _place_order_kwargs(
    clients: OkxClients,
    *,
    position_side: str,
    order_side: str,
    inst_id: str,
    sz: float,
    ord_type: str = "market",
    reduce_only: bool = False,
    cl_ord_id: str = "",
) -> dict[str, str]:
    pos_mode = get_pos_mode(clients.account, clients.settings)
    kwargs: dict[str, str] = {
        "instId": inst_id,
        "tdMode": TD_MODE,
        "side": order_side,
        "ordType": ord_type,
        "sz": _fmt_num(sz),
        "posSide": _order_pos_side(position_side, pos_mode),
    }
    if cl_ord_id:
        kwargs["clOrdId"] = cl_ord_id
    if reduce_only:
        kwargs["reduceOnly"] = "true"
    return kwargs


def place_market_entry(
    clients: OkxClients,
    *,
    symbol: str,
    side: str,
    quantity: float,
    cl_ord_id: str = "",
) -> dict[str, Any]:
    """市價開倉；quantity 為幣數（非張數）。"""
    inst_id = to_inst_id(symbol)
    instrument = get_instrument(clients.public, inst_id)
    sz = coin_qty_to_contracts(quantity, instrument)
    kwargs = _place_order_kwargs(
        clients,
        position_side=side,
        order_side=_entry_side(side),
        inst_id=inst_id,
        sz=sz,
        cl_ord_id=cl_ord_id,
    )

    def _place() -> dict[str, Any]:
        _throttle_okx_write()
        response = clients.trade.place_order(**kwargs)
        return _algo_row(response, context=f"{inst_id} 市價進場")

    row = call_okx_with_retry(
        _place,
        context=f"{inst_id} 市價進場",
        max_attempts=5,
        base_delay_sec=1.0,
    )
    log.info(f"已下單 {_entry_side(side)} {sz} 張 {inst_id}")
    return row


def place_market_reduce(
    clients: OkxClients,
    *,
    symbol: str,
    side: str,
    quantity: float,
) -> dict[str, Any] | None:
    """市價減倉（reduceOnly）；quantity 為幣數。"""
    inst_id = to_inst_id(symbol)
    instrument = get_instrument(clients.public, inst_id)
    try:
        sz = coin_qty_to_contracts(quantity, instrument)
    except ValueError:
        return None
    if sz <= 0:
        return None
    kwargs = _place_order_kwargs(
        clients,
        position_side=side,
        order_side=_exit_side(side),
        inst_id=inst_id,
        sz=sz,
        reduce_only=True,
    )
    response = clients.trade.place_order(**kwargs)
    row = _algo_row(response, context=f"{inst_id} 市價減倉")
    return row


def place_stop_algo(
    clients: OkxClients,
    *,
    symbol: str,
    side: str,
    trigger_price: float,
    quantity: float,
    trigger_px_type: TriggerPxType = "mark",
    client_algo_id: str = "",
) -> dict[str, Any]:
    """只掛止損 conditional algo（市價觸發）。"""
    return _place_conditional_algo(
        clients,
        symbol=symbol,
        position_side=side,
        exit_side=_exit_side(side),
        quantity=quantity,
        order_kind="stop",
        trigger_price=trigger_price,
        trigger_px_type=trigger_px_type,
        client_algo_id=client_algo_id,
    )


def place_tp_algo(
    clients: OkxClients,
    *,
    symbol: str,
    side: str,
    trigger_price: float,
    quantity: float,
    trigger_px_type: TriggerPxType = "mark",
    client_algo_id: str = "",
) -> dict[str, Any]:
    """只掛止盈 conditional algo（市價觸發）。"""
    return _place_conditional_algo(
        clients,
        symbol=symbol,
        position_side=side,
        exit_side=_exit_side(side),
        quantity=quantity,
        order_kind="take_profit",
        trigger_price=trigger_price,
        trigger_px_type=trigger_px_type,
        client_algo_id=client_algo_id,
    )


def place_algo_conditional_order(
    clients: OkxClients,
    *,
    symbol: str,
    side: str,
    order_type: str,
    trigger_price: float,
    quantity: float,
    client_algo_id: str | None = None,
    position_side: str = "long",
) -> dict[str, Any]:
    """
    與 Binance 介面對齊的條件單封裝。
    side: BUY/SELL（平倉方向）；order_type: STOP_MARKET / TAKE_PROFIT_MARKET。
    position_side: 持倉方向 long/short（用於推斷 exit side 校驗）。
    """
    exit_side = side.lower()
    if exit_side in ("buy", "sell"):
        order_side = exit_side
    else:
        order_side = _exit_side(position_side)
    kind = "stop" if "STOP" in order_type.upper() else "take_profit"
    return _place_conditional_algo(
        clients,
        symbol=symbol,
        position_side=position_side,
        exit_side=order_side,
        quantity=quantity,
        order_kind=kind,
        trigger_price=trigger_price,
        client_algo_id=client_algo_id or "",
    )


def _place_conditional_algo(
    clients: OkxClients,
    *,
    symbol: str,
    position_side: str,
    exit_side: str,
    quantity: float,
    order_kind: Literal["stop", "take_profit"],
    trigger_price: float,
    trigger_px_type: TriggerPxType = "mark",
    client_algo_id: str = "",
) -> dict[str, Any]:
    inst_id = to_inst_id(symbol)
    instrument = get_instrument(clients.public, inst_id)
    sz = coin_qty_to_contracts(quantity, instrument)
    px = _round_price(trigger_price, instrument)
    pos_mode = get_pos_mode(clients.account, clients.settings)
    kwargs: dict[str, str] = {
        "instId": inst_id,
        "tdMode": TD_MODE,
        "side": exit_side,
        "ordType": "conditional",
        "sz": _fmt_num(sz),
        "reduceOnly": "true",
        "algoClOrdId": client_algo_id or "",
        "posSide": _order_pos_side(position_side, pos_mode),
    }
    if order_kind == "stop":
        kwargs.update(
            slTriggerPx=_fmt_num(px),
            slOrdPx="-1",
            slTriggerPxType=trigger_px_type,
        )
        label = "止損"
    else:
        kwargs.update(
            tpTriggerPx=_fmt_num(px),
            tpOrdPx="-1",
            tpTriggerPxType=trigger_px_type,
        )
        label = "止盈"

    response = clients.trade.place_algo_order(**kwargs)
    row = _algo_row(response, context=f"{inst_id} {label}")
    algo_id = row.get("algoId")
    log.info(f"{inst_id} {label} algo @ {px}  sz={sz} 張  algoId={algo_id}")
    return {"algoId": algo_id, **row}


def amend_stop_algo(
    clients: OkxClients,
    algo_id: str | int,
    *,
    symbol: str,
    new_stop: float,
    quantity: float | None = None,
    trigger_px_type: TriggerPxType = "mark",
) -> dict[str, Any]:
    """修改止損觸發價（優先於 cancel+place）。"""
    inst_id = to_inst_id(symbol)
    instrument = get_instrument(clients.public, inst_id)
    px = _round_price(new_stop, instrument)
    kwargs: dict[str, str] = {
        "instId": inst_id,
        "algoId": str(algo_id),
        "newSlTriggerPx": _fmt_num(px),
        "newSlOrdPx": "-1",
        "newSlTriggerPxType": trigger_px_type,
    }
    if quantity is not None and quantity > 0:
        sz = coin_qty_to_contracts(quantity, instrument)
        kwargs["newSz"] = _fmt_num(sz)
    response = clients.trade.amend_algo_order(**kwargs)
    row = _algo_row(response, context=f"{inst_id} 修改止損")
    log.info(f"{inst_id} 止損 amend → {px}  algoId={algo_id}")
    return row


def amend_tp_algo(
    clients: OkxClients,
    algo_id: str | int,
    *,
    symbol: str,
    new_tp: float,
    quantity: float | None = None,
    trigger_px_type: TriggerPxType = "mark",
) -> dict[str, Any]:
    """修改止盈觸發價。"""
    inst_id = to_inst_id(symbol)
    instrument = get_instrument(clients.public, inst_id)
    px = _round_price(new_tp, instrument)
    kwargs: dict[str, str] = {
        "instId": inst_id,
        "algoId": str(algo_id),
        "newTpTriggerPx": _fmt_num(px),
        "newTpOrdPx": "-1",
        "newTpTriggerPxType": trigger_px_type,
    }
    if quantity is not None and quantity > 0:
        sz = coin_qty_to_contracts(quantity, instrument)
        kwargs["newSz"] = _fmt_num(sz)
    response = clients.trade.amend_algo_order(**kwargs)
    row = _algo_row(response, context=f"{inst_id} 修改止盈")
    log.info(f"{inst_id} 止盈 amend → {px}  algoId={algo_id}")
    return row


def cancel_algo_order(
    trade: Any,
    algo_id: str | int,
    *,
    symbol: str | None = None,
) -> dict[str, Any] | None:
    """取消單張 conditional algo 單。"""
    entry: dict[str, str] = {"algoId": str(algo_id)}
    if symbol:
        entry["instId"] = to_inst_id(symbol)
    try:
        response = trade.cancel_algo_order([entry])
        ensure_ok_response(response, context=f"取消 algo {algo_id}")
        rows = response.get("data") or []
        return rows[0] if rows else {"algoId": str(algo_id)}
    except Exception as e:
        log.warning(f"取消 Algo {algo_id} 失敗: {format_okx_error(e)}")
        return None


def fetch_open_algo_orders(
    trade: Any,
    symbol: str | None = None,
) -> list[dict[str, Any]]:
    """查詢未觸發的 conditional algo 單。"""
    kwargs: dict[str, str] = {"ordType": "conditional", "instType": "SWAP"}
    if symbol:
        kwargs["instId"] = to_inst_id(symbol)
    try:
        response = ensure_ok_response(
            trade.order_algos_list(**kwargs),
            context="查詢 algo 掛單",
        )
        return list(response.get("data") or [])
    except Exception as e:
        log.warning(f"讀取 Algo 掛單失敗: {format_okx_error(e)}")
        return []


def verify_connection(settings: OkxFuturesSettings) -> bool:
    if not settings.api_key or not settings.api_secret or not settings.passphrase:
        from core.account_profiles import load_profile

        profile = load_profile(settings.account_id, settings.exec_mode)
        log.error(f"缺少 API 金鑰。{credentials_hint_for_profile(profile)}")
        return False
    if not HAS_OKX_CLIENT:
        log.error("請安裝 python-okx: pip install python-okx")
        return False

    net = mode_label(settings.exec_mode)
    demo = "Demo" if settings.testnet else "主網"
    try:
        clients = create_clients(settings)
        equity, available = fetch_usdt_balance(clients.account)
        positions = fetch_swap_positions(clients.account)
        log.info(
            f"✅ 已連線 OKX 永續 {net}（{demo}）  "
            f"USDT 權益: {equity:.2f}  可用: {available:.2f}  "
            f"持倉數: {len(positions)}"
        )
        return True
    except Exception as e:
        log.error(f"❌ OKX {net} 連線失敗: {format_okx_error(e)}")
        if settings.testnet:
            log.error(
                "請確認金鑰來自 OKX Demo Trading → API（非主網 API Management）"
            )
        return False
