"""Binance USDT-M 永續帳戶與本地模擬帳戶：持倉、委託、成交、績效。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from typing import Any

import pandas as pd

from core.backtest_pnl import profit_factor_from_pnls
from core.account_profiles import AccountProfile, load_profile, profile_capital
from core.binance_credentials import ExecMode, credentials_configured, credentials_hint
from core.binance_futures import FuturesSettings, create_client, fetch_open_algo_orders
from core.order_tags import strategy_name_from_client_order_id


@dataclass
class AccountHeadline:
    """帳戶頂部即時指標（輕量 API，可高頻刷新）。"""

    wallet_balance: float = 0.0
    available_balance: float = 0.0
    unrealized_pnl: float = 0.0
    weighted_leverage: float | None = None
    position_count: int = 0
    open_order_count: int = 0
    error: str | None = None


@dataclass
class AccountView:
    mode: str
    wallet_balance: float
    available_balance: float
    unrealized_pnl: float
    weighted_leverage: float | None
    win_rate: float | None
    profit_factor: float | None
    realized_pnl: float
    commission: float
    funding: float
    position_count: int
    open_order_count: int
    positions: pd.DataFrame
    open_orders: pd.DataFrame
    trades: pd.DataFrame
    income: pd.DataFrame
    strategy_stats: pd.DataFrame
    account_id: str = "account1"
    account_label: str = ""
    error: str | None = None
    warnings: list[str] = field(default_factory=list)
    stats_reset_at: str | None = None


def _float(val: Any, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _ms_to_iso(ms: Any) -> str:
    """內部儲存用 UTC ISO（篩選／統計用）；顯示時再轉台北時間。"""
    if ms is None:
        return ""
    try:
        ts = int(ms)
        return pd.to_datetime(ts, unit="ms", utc=True).isoformat()
    except (TypeError, ValueError):
        return str(ms)


def _format_api_error(exc: Exception) -> str:
    code = getattr(exc, "error_code", None)
    msg = getattr(exc, "error_message", None) or str(exc)
    if code is not None:
        return f"Binance API {code}: {msg}"
    return msg or type(exc).__name__


def _strategy_name(strategy_id: str) -> str:
    from core.strategy_registry import STRATEGIES

    return STRATEGIES[strategy_id].name if strategy_id in STRATEGIES else str(strategy_id)


def bot_orders_for_profile(profile: AccountProfile, limit: int = 500) -> pd.DataFrame:
    from core.order_executor import list_orders_for_profile

    orders = list_orders_for_profile(profile, limit=limit)
    if orders.empty:
        return pd.DataFrame()
    out = orders.copy()
    if "strategy_id" in out.columns:
        out["strategy_name"] = out["strategy_id"].map(_strategy_name)
    if "leverage" in out.columns:
        out["leverage"] = pd.to_numeric(out["leverage"], errors="coerce")
    return out.sort_values("created_at", ascending=False).reset_index(drop=True)


def bot_orders_for_mode(
    mode: ExecMode | str,
    limit: int = 500,
    account_id: str = "account1",
) -> pd.DataFrame:
    if isinstance(mode, str):
        mode = ExecMode(mode)
    return bot_orders_for_profile(load_profile(account_id, mode), limit=limit)


def weighted_position_leverage(positions: pd.DataFrame) -> float | None:
    if positions.empty or "leverage" not in positions.columns:
        return None
    if "size" in positions.columns and "mark_price" in positions.columns:
        notional = positions["size"].astype(float) * positions["mark_price"].astype(float)
        total = notional.sum()
        if total > 0:
            return float((notional * positions["leverage"].astype(float)).sum() / total)
    lev = positions["leverage"].astype(float)
    return float(lev.mean()) if len(lev) else None


def _leverage_lookup(positions: pd.DataFrame, bot_orders: pd.DataFrame) -> dict[str, float]:
    lookup: dict[str, float] = {}
    if not positions.empty and "symbol" in positions.columns and "leverage" in positions.columns:
        for _, row in positions.iterrows():
            lookup[str(row["symbol"])] = _float(row["leverage"], 1)
    if not bot_orders.empty and "symbol" in bot_orders.columns and "leverage" in bot_orders.columns:
        for _, row in bot_orders.iterrows():
            sym = str(row["symbol"]).upper()
            lev = _float(row.get("leverage"), 0)
            if lev > 0 and sym not in lookup:
                lookup[sym] = lev
    return lookup


def attach_leverage_to_orders(
    open_orders: pd.DataFrame,
    positions: pd.DataFrame,
    bot_orders: pd.DataFrame,
) -> pd.DataFrame:
    if open_orders.empty:
        return open_orders
    out = open_orders.copy()
    lookup = _leverage_lookup(positions, bot_orders)
    out["leverage"] = out["symbol"].map(lambda s: lookup.get(str(s), None))
    return out


def attach_entry_meta_to_orders(
    open_orders: pd.DataFrame,
    positions: pd.DataFrame,
) -> pd.DataFrame:
    """掛單表補上對應持倉的進場價、開單時間、策略名。"""
    if open_orders.empty:
        return open_orders
    out = open_orders.copy()
    entry_map: dict[str, float] = {}
    time_map: dict[str, str] = {}
    strat_map: dict[str, str] = {}
    if not positions.empty:
        for _, row in positions.iterrows():
            sym = str(row["symbol"]).upper()
            entry_map[sym] = _float(row.get("entry_price"))
            time_map[sym] = str(row.get("open_time", "") or "")
            strat_map[sym] = str(row.get("strategy_name", "") or "")
    sym_u = out["symbol"].astype(str).str.upper()
    out["entry_price"] = sym_u.map(entry_map)
    out["open_time"] = sym_u.map(time_map)
    if "strategy_name" not in out.columns:
        out["strategy_name"] = sym_u.map(strat_map)
    return out


def _fetch_orders_by_symbol(
    client: Any,
    symbols: set[str],
    *,
    order_limit: int = 500,
) -> dict[str, list[dict]]:
    """依交易對拉取歷史訂單（含 clientOrderId）。"""
    out: dict[str, list[dict]] = {}
    for sym in sorted(symbols):
        try:
            raw = client.get_all_orders(symbol=sym, limit=order_limit)
        except Exception:
            continue
        if isinstance(raw, list) and raw:
            out[sym] = raw
    return out


def _order_cid_map_from_orders(orders_by_symbol: dict[str, list[dict]]) -> dict[str, str]:
    order_cid: dict[str, str] = {}
    for orders in orders_by_symbol.values():
        for o in orders:
            oid = str(o.get("orderId", "") or "")
            cid = str(o.get("clientOrderId") or o.get("origClientOrderId") or "")
            if oid and cid:
                order_cid[oid] = cid
    return order_cid


def _best_entry_order(
    orders: list[dict],
    *,
    side: str,
    entry_price: float = 0.0,
) -> tuple[str, float, int]:
    """依進場方向與進場價，對到最佳匹配的市價進場單 → (策略名, 進場價, time_ms)。"""
    entry_side = "BUY" if side == "long" else "SELL"
    tagged: list[tuple[str, float, int]] = []
    for o in orders:
        if o.get("side") != entry_side or o.get("type") != "MARKET":
            continue
        if o.get("status") not in ("FILLED", "PARTIALLY_FILLED"):
            continue
        name = strategy_name_from_client_order_id(str(o.get("clientOrderId") or ""))
        avg_p = _float(o.get("avgPrice") or o.get("price"))
        t_ms = int(o.get("time") or o.get("updateTime") or 0)
        tagged.append((name, avg_p, t_ms))
    if not tagged:
        return "", 0.0, 0
    if entry_price > 0:
        tagged.sort(key=lambda x: (abs(x[1] - entry_price) / max(entry_price, 1e-12), -x[2]))
    else:
        tagged.sort(key=lambda x: -x[2])
    return tagged[0]


def _strategy_from_entry_orders(
    orders: list[dict],
    *,
    side: str,
    entry_price: float = 0.0,
) -> str:
    name, _, _ = _best_entry_order(orders, side=side, entry_price=entry_price)
    return name


def attach_strategy_to_positions(
    positions: pd.DataFrame,
    orders_by_symbol: dict[str, list[dict]],
    bot_orders: pd.DataFrame,
) -> pd.DataFrame:
    if positions.empty:
        return positions
    out = positions.copy()
    bot_sym: dict[str, str] = {}
    if not bot_orders.empty:
        ordered = bot_orders.sort_values("created_at") if "created_at" in bot_orders.columns else bot_orders
        for _, row in ordered.iterrows():
            sym = str(row["symbol"]).upper()
            n = row.get("strategy_name") or _strategy_name(str(row.get("strategy_id", "")))
            if n:
                bot_sym[sym] = str(n)

    names: list[str] = []
    open_times: list[str] = []
    entry_prices: list[float | None] = []
    for _, row in out.iterrows():
        sym = str(row["symbol"]).upper()
        ep = _float(row.get("entry_price"))
        orders = orders_by_symbol.get(sym, [])
        name, order_ep, t_ms = _best_entry_order(
            orders,
            side=str(row["side"]),
            entry_price=ep,
        )
        if not name:
            name = bot_sym.get(sym, "")
        open_ts = _ms_to_iso(t_ms) if t_ms else ""
        if not open_ts and not bot_orders.empty and "created_at" in bot_orders.columns:
            matches = bot_orders[bot_orders["symbol"].astype(str).str.upper() == sym]
            if not matches.empty:
                open_ts = str(matches["created_at"].iloc[-1])
        names.append(name)
        open_times.append(open_ts)
        entry_prices.append(order_ep if order_ep > 0 else (ep if ep > 0 else None))
    out["strategy_name"] = names
    out["open_time"] = open_times
    out["entry_price"] = entry_prices
    return out


def _symbol_strategy_from_positions(positions: pd.DataFrame) -> dict[str, str]:
    if positions.empty or "strategy_name" not in positions.columns:
        return {}
    out: dict[str, str] = {}
    for _, row in positions.iterrows():
        sym = str(row["symbol"]).upper()
        name = str(row.get("strategy_name", "") or "").strip()
        if name:
            out[sym] = name
    return out


def attach_leverage_and_strategy_to_trades(
    trades: pd.DataFrame,
    positions: pd.DataFrame,
    bot_orders: pd.DataFrame,
    *,
    order_cid_map: dict[str, str] | None = None,
    symbol_strategy: dict[str, str] | None = None,
) -> pd.DataFrame:
    if trades.empty:
        return trades
    out = trades.copy()
    sym_lev = _leverage_lookup(positions, bot_orders)

    order_cid_map = order_cid_map or {}
    symbol_strategy = symbol_strategy or {}

    order_lev: dict[str, float] = {}
    order_strategy: dict[str, str] = {}
    if not bot_orders.empty:
        for _, row in bot_orders.iterrows():
            oid = str(row.get("exchange_order_id", "") or "")
            if oid:
                lev = _float(row.get("leverage"), 0)
                if lev > 0:
                    order_lev[oid] = lev
                sid = row.get("strategy_id", "")
                name = row.get("strategy_name") or _strategy_name(str(sid))
                if name:
                    order_strategy[oid] = str(name)

    leverages: list[float | None] = []
    strategies: list[str] = []
    for _, row in out.iterrows():
        oid = str(row.get("order_id", "") or "")
        sym = str(row.get("symbol", "")).upper()
        lev = order_lev.get(oid) or sym_lev.get(sym)
        leverages.append(lev)
        cid = str(row.get("client_order_id", "") or "") or order_cid_map.get(oid, "")
        name = strategy_name_from_client_order_id(cid)
        if not name:
            name = order_strategy.get(oid, "")
        if not name:
            name = symbol_strategy.get(sym, "")
        strategies.append(name)

    out["leverage"] = leverages
    out["strategy_name"] = strategies
    # 平倉成交通常無 clientOrderId：沿用同 symbol 已辨識的策略
    sym_strategy: dict[str, str] = {}
    for name, sym in zip(out["strategy_name"], out["symbol"].astype(str).str.upper()):
        if name:
            sym_strategy[sym] = name
    out["strategy_name"] = [
        (n or sym_strategy.get(str(s).upper(), ""))
        for n, s in zip(out["strategy_name"], out["symbol"])
    ]
    if "realized_pnl" in out.columns:
        out["is_win"] = out["realized_pnl"].apply(lambda x: x > 0 if _float(x) != 0 else None)
    return out


def compute_trade_stats(trades: pd.DataFrame) -> tuple[float | None, float | None]:
    if trades.empty or "realized_pnl" not in trades.columns:
        return None, None
    closed = trades[trades["realized_pnl"].astype(float) != 0]
    if closed.empty:
        return None, None
    pnls = closed["realized_pnl"].astype(float).tolist()
    wins = sum(1 for p in pnls if p > 0)
    win_rate = wins / len(pnls) if pnls else None
    pf = profit_factor_from_pnls(pnls)
    if pf == float("inf"):
        pf = 9999.0
    return win_rate, pf


def _strategy_stats_from_positions(positions: pd.DataFrame) -> pd.DataFrame:
    """持倉有策略、成交表尚無資料時，從持倉組策略績效列。"""
    if positions.empty or "strategy_name" not in positions.columns:
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    for _, row in positions.iterrows():
        name = str(row.get("strategy_name", "") or "").strip()
        if not name:
            continue
        rows.append(
            {
                "strategy_name": name,
                "symbol": row.get("symbol", ""),
                "side": row.get("side", ""),
                "leverage": _float(row.get("leverage"), 0) or None,
                "trade_count": 0,
                "win_rate": None,
                "profit_factor": None,
                "avg_price": _float(row.get("entry_price"), 0) or None,
                "entry_price": _float(row.get("entry_price"), 0) or None,
                "open_time": row.get("open_time", ""),
                "realized_pnl": 0.0,
            }
        )
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def merge_strategy_performance(trades: pd.DataFrame, positions: pd.DataFrame) -> pd.DataFrame:
    """合併成交績效、持倉策略，並補齊全部策略空白列。"""
    stats = strategy_performance_table(trades)
    pos_stats = _strategy_stats_from_positions(positions)
    if not pos_stats.empty:
        if stats.empty:
            stats = pos_stats.copy()
        else:
            existing = set(
                zip(
                    stats["strategy_name"].astype(str),
                    stats["symbol"].astype(str),
                    stats["side"].astype(str),
                )
            )
            extra_rows = [
                row.to_dict()
                for _, row in pos_stats.iterrows()
                if (str(row["strategy_name"]), str(row["symbol"]), str(row["side"])) not in existing
            ]
            if extra_rows:
                stats = pd.concat([stats, pd.DataFrame(extra_rows)], ignore_index=True)
    return complete_all_strategy_rows(stats)


def complete_all_strategy_rows(stats: pd.DataFrame) -> pd.DataFrame:
    """確保全部策略皆有一列（尚無成交顯示 0 筆）。"""
    from core.strategy_registry import STRATEGIES

    base = stats.copy() if stats is not None and not stats.empty else pd.DataFrame()
    present: set[str] = set()
    if not base.empty and "strategy_name" in base.columns:
        present = set(base["strategy_name"].astype(str).tolist())

    placeholders: list[dict[str, Any]] = []
    for meta in STRATEGIES.values():
        if meta.name in present:
            continue
        placeholders.append(
            {
                "strategy_name": meta.name,
                "symbol": "—",
                "side": "—",
                "leverage": None,
                "trade_count": 0,
                "win_rate": None,
                "profit_factor": None,
                "avg_price": None,
                "entry_price": None,
                "open_time": "",
                "realized_pnl": 0.0,
            }
        )
    if placeholders:
        base = pd.concat([base, pd.DataFrame(placeholders)], ignore_index=True)
    if base.empty:
        return base
    return base.sort_values("strategy_name").reset_index(drop=True)


def strategy_performance_table(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    df = trades.copy()
    if "strategy_name" not in df.columns:
        df["strategy_name"] = "未知"
    df["strategy_name"] = df["strategy_name"].replace("", "未知")

    rows: list[dict[str, Any]] = []
    for (strategy, symbol, side), grp in df.groupby(
        ["strategy_name", "symbol", "side"], dropna=False
    ):
        closed = grp[grp["realized_pnl"].astype(float) != 0] if "realized_pnl" in grp.columns else pd.DataFrame()
        pnls = closed["realized_pnl"].astype(float).tolist() if not closed.empty else []
        wr, pf = compute_trade_stats(closed if not closed.empty else grp)
        lev_vals = grp["leverage"].dropna() if "leverage" in grp.columns else pd.Series(dtype=float)
        leverage = float(lev_vals.mean()) if len(lev_vals) else None
        avg_price = float(grp["avg_price"].mean()) if "avg_price" in grp.columns and len(grp) else None
        if avg_price is None and "price" in grp.columns:
            qty = grp["quantity"].astype(float) if "quantity" in grp.columns else pd.Series([1.0] * len(grp))
            total_qty = qty.sum()
            avg_price = float((grp["price"].astype(float) * qty).sum() / total_qty) if total_qty > 0 else None

        opens = (
            grp[grp["realized_pnl"].astype(float) == 0]
            if "realized_pnl" in grp.columns
            else grp
        )
        ref = opens if not opens.empty else grp
        open_time = ""
        if "time" in ref.columns and not ref.empty:
            open_time = str(ref["time"].min())
        entry_price = None
        if "avg_price" in ref.columns and not ref.empty:
            entry_price = float(ref.iloc[0]["avg_price"])
        if entry_price is None:
            entry_price = avg_price

        rows.append(
            {
                "strategy_name": strategy,
                "symbol": symbol,
                "side": side,
                "leverage": leverage,
                "trade_count": len(grp),
                "win_rate": wr,
                "profit_factor": pf,
                "avg_price": avg_price,
                "entry_price": entry_price,
                "open_time": open_time,
                "realized_pnl": float(closed["realized_pnl"].sum()) if not closed.empty else 0.0,
            }
        )
    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(rows)
    return out.sort_values(["strategy_name", "symbol"]).reset_index(drop=True)


def summarize_income(income: pd.DataFrame) -> dict[str, float]:
    if income.empty or "income_type" not in income.columns:
        return {"realized_pnl": 0.0, "commission": 0.0, "funding": 0.0, "net": 0.0}
    realized = income.loc[income["income_type"] == "REALIZED_PNL", "income"].sum()
    commission = income.loc[income["income_type"] == "COMMISSION", "income"].sum()
    funding = income.loc[income["income_type"] == "FUNDING_FEE", "income"].sum()
    return {
        "realized_pnl": float(realized),
        "commission": float(commission),
        "funding": float(funding),
        "net": float(realized + commission + funding),
    }


def _positions_df(rows: list[dict]) -> pd.DataFrame:
    records = []
    for r in rows:
        amt = _float(r.get("positionAmt"))
        if amt == 0:
            continue
        records.append(
            {
                "symbol": r.get("symbol", ""),
                "side": "long" if amt > 0 else "short",
                "size": abs(amt),
                "entry_price": _float(r.get("entryPrice")),
                "mark_price": _float(r.get("markPrice")),
                "unrealized_pnl": _float(r.get("unRealizedProfit")),
                "leverage": int(_float(r.get("leverage"), 1)),
                "margin_type": r.get("marginType", ""),
                "liquidation_price": _float(r.get("liquidationPrice")),
            }
        )
    if not records:
        return pd.DataFrame()
    return pd.DataFrame(records).sort_values("symbol").reset_index(drop=True)


def _open_orders_df(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    records = []
    for r in rows:
        records.append(
            {
                "symbol": r.get("symbol", ""),
                "type": r.get("type", ""),
                "side": r.get("side", ""),
                "price": _float(r.get("price")),
                "stop_price": _float(r.get("stopPrice")),
                "quantity": _float(r.get("origQty")),
                "filled": _float(r.get("executedQty")),
                "reduce_only": r.get("reduceOnly", False),
                "status": r.get("status", ""),
                "order_id": r.get("orderId", ""),
                "client_order_id": r.get("clientOrderId", ""),
                "time": _ms_to_iso(r.get("time")),
            }
        )
    return pd.DataFrame(records).sort_values("time", ascending=False).reset_index(drop=True)


def _aggregate_trades_by_order(
    rows: list[dict],
    order_cid_map: dict[str, str] | None = None,
) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    if df.empty:
        return df

    grouped: list[dict[str, Any]] = []
    for order_id, grp in df.groupby("orderId", sort=False):
        qty = grp["qty"].astype(float)
        total_qty = qty.sum()
        avg_price = float((grp["price"].astype(float) * qty).sum() / total_qty) if total_qty > 0 else 0.0
        realized = float(grp["realizedPnl"].astype(float).sum()) if "realizedPnl" in grp.columns else 0.0
        commission = float(grp["commission"].astype(float).sum()) if "commission" in grp.columns else 0.0
        first = grp.iloc[0]
        side = first.get("side", "")
        oid = str(order_id)
        cid = str(first.get("clientOrderId", "") or "")
        if not cid and order_cid_map:
            cid = order_cid_map.get(oid, "")
        grouped.append(
            {
                "time": _ms_to_iso(grp["time"].max()),
                "symbol": first.get("symbol", ""),
                "side": side,
                "direction": "long" if side == "BUY" else "short",
                "avg_price": avg_price,
                "quantity": total_qty,
                "quote_qty": float(grp["quoteQty"].astype(float).sum()) if "quoteQty" in grp.columns else 0.0,
                "realized_pnl": realized,
                "commission": commission,
                "commission_asset": first.get("commissionAsset", ""),
                "order_id": order_id,
                "client_order_id": cid,
                "trade_count": len(grp),
            }
        )
    out = pd.DataFrame(grouped)
    return out.sort_values("time", ascending=False).reset_index(drop=True)


def _collect_symbols(
    positions: pd.DataFrame,
    open_orders: pd.DataFrame,
    income: pd.DataFrame,
    bot_orders: pd.DataFrame,
) -> set[str]:
    symbols: set[str] = set()
    for df, col in (
        (positions, "symbol"),
        (open_orders, "symbol"),
        (income, "symbol"),
        (bot_orders, "symbol"),
    ):
        if not df.empty and col in df.columns:
            symbols.update(df[col].astype(str).str.upper().tolist())
    return {s for s in symbols if s and s != "nan"}


def _normalize_algo_open_order(row: dict) -> dict:
    """將 Algo 條件單對齊一般 open order 欄位。"""
    return {
        "symbol": row.get("symbol", ""),
        "type": row.get("orderType") or row.get("type", ""),
        "side": row.get("side", ""),
        "price": row.get("price", 0),
        "stopPrice": row.get("triggerPrice"),
        "origQty": row.get("quantity"),
        "executedQty": 0,
        "reduceOnly": True,
        "status": row.get("algoStatus", ""),
        "orderId": row.get("algoId", ""),
        "clientOrderId": row.get("clientAlgoId", ""),
        "time": row.get("createTime") or row.get("updateTime"),
    }


def _fetch_all_open_orders(client: Any, symbols: set[str]) -> list[dict]:
    """
    查詢全部掛單（含 Algo 條件單）。
    binance-futures-connector 的 get_open_orders(symbol) 為單筆查詢；
    get_orders() 對應 GET /fapi/v1/openOrders，可不帶 symbol。
    """
    rows: list[dict] = []
    try:
        part = client.get_orders()
        if isinstance(part, list):
            rows.extend(part)
    except Exception:
        for sym in sorted(symbols):
            try:
                part = client.get_orders(symbol=sym)
                if isinstance(part, list):
                    rows.extend(part)
            except Exception:
                continue

    for algo in fetch_open_algo_orders(client):
        rows.append(_normalize_algo_open_order(algo))

    return rows


def _income_df(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    records = []
    for r in rows:
        records.append(
            {
                "time": _ms_to_iso(r.get("time")),
                "symbol": r.get("symbol", ""),
                "income_type": r.get("incomeType", ""),
                "income": _float(r.get("income")),
                "asset": r.get("asset", ""),
                "info": r.get("info", ""),
                "trade_id": r.get("tradeId", ""),
            }
        )
    df = pd.DataFrame(records)
    return df.sort_values("time", ascending=False).reset_index(drop=True)


def fetch_live_headline(
    mode: ExecMode,
    account_id: str = "account1",
) -> AccountHeadline:
    """輕量即時帳戶摘要（向後相容）。"""
    return fetch_live_headline_for_profile(load_profile(account_id, mode))


def fetch_live_headline_for_profile(profile: AccountProfile) -> AccountHeadline:
    """
    輕量即時帳戶摘要：account + position_risk + 掛單數。
    供 Streamlit 頂部指標高頻刷新（未實現損益隨標記價浮動）。
    """
    empty = AccountHeadline()
    if profile.network == ExecMode.PAPER:
        capital = profile_capital(profile)
        view = fetch_paper_account(account_id=profile.account_id)
        return AccountHeadline(
            wallet_balance=capital,
            available_balance=capital,
            unrealized_pnl=view.unrealized_pnl,
            weighted_leverage=view.weighted_leverage,
            position_count=view.position_count,
            open_order_count=view.open_order_count,
        )

    if not credentials_configured(profile.network, profile.account_id):
        empty.error = credentials_hint(profile.network, profile.account_id)
        return empty

    try:
        settings = FuturesSettings.from_profile(profile)
        client = create_client(settings)
        acct = client.account()
        assets = acct.get("assets", [])
        usdt = next((a for a in assets if a.get("asset") == "USDT"), {})
        wallet = _float(usdt.get("walletBalance"))
        available = _float(usdt.get("availableBalance"))

        positions = _positions_df(client.get_position_risk())
        unrealized = float(positions["unrealized_pnl"].sum()) if not positions.empty else 0.0
        lev = weighted_position_leverage(positions)

        symbols = (
            set(positions["symbol"].astype(str).str.upper())
            if not positions.empty
            else set()
        )
        open_count = len(_fetch_all_open_orders(client, symbols))

        return AccountHeadline(
            wallet_balance=wallet,
            available_balance=available,
            unrealized_pnl=unrealized,
            weighted_leverage=lev,
            position_count=len(positions),
            open_order_count=open_count,
        )
    except Exception as e:
        empty.error = _format_api_error(e)
        return empty


def apply_stats_reset(view: AccountView, profile: AccountProfile) -> AccountView:
    """
    依統計歸零點過濾成交／損益，重算勝率與獲利因子。
    持倉、掛單、錢包餘額不受影響。
    """
    from core.stats_reset import filter_dataframe_after_reset, get_stats_reset_at, get_stats_reset_label

    reset_at = get_stats_reset_at(profile)
    if reset_at is None:
        return view

    trades = filter_dataframe_after_reset(view.trades, "time", reset_at)
    income = filter_dataframe_after_reset(view.income, "time", reset_at)
    income_sum = summarize_income(income)
    win_rate, pf = compute_trade_stats(trades)
    strategy_stats = merge_strategy_performance(trades, view.positions)

    return replace(
        view,
        trades=trades,
        income=income,
        win_rate=win_rate,
        profit_factor=pf,
        realized_pnl=income_sum["realized_pnl"],
        commission=income_sum["commission"],
        funding=income_sum["funding"],
        strategy_stats=strategy_stats,
        stats_reset_at=get_stats_reset_label(profile),
    )


def fetch_account(profile: AccountProfile, **kwargs: Any) -> AccountView:
    """依 profile 拉取帳戶（paper / testnet / live）。"""
    if profile.network == ExecMode.PAPER:
        view = fetch_paper_account(account_id=profile.account_id, **kwargs)
    else:
        view = fetch_futures_account(
            profile.network, account_id=profile.account_id, **kwargs
        )
    return apply_stats_reset(view, profile)


def fetch_futures_account(
    mode: ExecMode,
    *,
    account_id: str = "account1",
    trade_limit_per_symbol: int = 50,
    income_limit: int = 200,
) -> AccountView:
    """從 Binance 永續 API 拉取 testnet / live 帳戶。"""
    profile = load_profile(account_id, mode)
    empty = AccountView(
        mode=mode.value,
        account_id=profile.account_id,
        account_label=profile.label,
        wallet_balance=0.0,
        available_balance=0.0,
        unrealized_pnl=0.0,
        weighted_leverage=None,
        win_rate=None,
        profit_factor=None,
        realized_pnl=0.0,
        commission=0.0,
        funding=0.0,
        position_count=0,
        open_order_count=0,
        positions=pd.DataFrame(),
        open_orders=pd.DataFrame(),
        trades=pd.DataFrame(),
        income=pd.DataFrame(),
        strategy_stats=pd.DataFrame(),
    )
    if not credentials_configured(mode, account_id):
        empty.error = credentials_hint(mode, account_id)
        return empty

    warnings: list[str] = []
    try:
        settings = FuturesSettings.from_profile(profile)
        client = create_client(settings)
    except Exception as e:
        empty.error = _format_api_error(e)
        return empty

    bot_orders = bot_orders_for_profile(profile)

    try:
        acct = client.account()
        assets = acct.get("assets", [])
        usdt = next((a for a in assets if a.get("asset") == "USDT"), {})
        wallet = _float(usdt.get("walletBalance"))
        available = _float(usdt.get("availableBalance"))
    except Exception as e:
        empty.error = f"帳戶讀取失敗: {_format_api_error(e)}"
        return empty

    try:
        positions = _positions_df(client.get_position_risk())
    except Exception as e:
        warnings.append(f"持倉讀取失敗: {_format_api_error(e)}")
        positions = pd.DataFrame()

    symbols = _collect_symbols(positions, pd.DataFrame(), pd.DataFrame(), bot_orders)

    try:
        open_orders = _open_orders_df(_fetch_all_open_orders(client, symbols))
        open_orders = attach_leverage_to_orders(open_orders, positions, bot_orders)
        open_orders = attach_entry_meta_to_orders(open_orders, positions)
    except Exception as e:
        warnings.append(f"掛單讀取失敗: {_format_api_error(e)}")
        open_orders = pd.DataFrame()

    try:
        income = _income_df(client.get_income_history(limit=income_limit))
    except Exception as e:
        warnings.append(f"損益紀錄讀取失敗: {_format_api_error(e)}")
        income = pd.DataFrame()
    income_sum = summarize_income(income)

    symbols = _collect_symbols(positions, open_orders, income, bot_orders)

    orders_by_symbol = _fetch_orders_by_symbol(client, symbols)
    order_cid_map = _order_cid_map_from_orders(orders_by_symbol)
    positions = attach_strategy_to_positions(positions, orders_by_symbol, bot_orders)
    symbol_strategy = _symbol_strategy_from_positions(positions)

    trade_rows: list[dict] = []
    trade_failures = 0
    for sym in sorted(symbols):
        try:
            for r in client.get_account_trades(symbol=sym, limit=trade_limit_per_symbol):
                row = dict(r)
                row["symbol"] = sym
                trade_rows.append(row)
        except Exception as e:
            trade_failures += 1
            if trade_failures <= 3:
                warnings.append(f"{sym} 成交讀取失敗: {_format_api_error(e)}")
    if not symbols:
        warnings.append(
            "無法判定交易對，成交明細為空（需有持倉、掛單或損益紀錄中的 symbol）。"
        )
    trades = _aggregate_trades_by_order(trade_rows, order_cid_map)
    trades = attach_leverage_and_strategy_to_trades(
        trades,
        positions,
        bot_orders,
        order_cid_map=order_cid_map,
        symbol_strategy=symbol_strategy,
    )
    if not open_orders.empty and "client_order_id" in open_orders.columns:
        open_orders["strategy_name"] = open_orders["client_order_id"].map(
            lambda cid: strategy_name_from_client_order_id(str(cid or ""))
        )

    win_rate, pf = compute_trade_stats(trades)
    strategy_stats = merge_strategy_performance(trades, positions)
    unrealized = float(positions["unrealized_pnl"].sum()) if not positions.empty else 0.0

    return AccountView(
        mode=mode.value,
        account_id=profile.account_id,
        account_label=profile.label,
        wallet_balance=wallet,
        available_balance=available,
        unrealized_pnl=unrealized,
        weighted_leverage=weighted_position_leverage(positions),
        win_rate=win_rate,
        profit_factor=pf,
        realized_pnl=income_sum["realized_pnl"],
        commission=income_sum["commission"],
        funding=income_sum["funding"],
        position_count=len(positions),
        open_order_count=len(open_orders),
        positions=positions,
        open_orders=open_orders,
        trades=trades,
        income=income,
        strategy_stats=strategy_stats,
        warnings=warnings,
    )


def fetch_paper_account(*, account_id: str = "account1", limit: int = 500) -> AccountView:
    """本地模擬帳戶：從 orders/{account_id}_paper.json 組裝。"""
    profile = load_profile(account_id, ExecMode.PAPER)
    bot_orders = bot_orders_for_profile(profile, limit=limit)
    capital = profile_capital(profile)

    if bot_orders.empty:
        return AccountView(
            mode=ExecMode.PAPER.value,
            account_id=profile.account_id,
            account_label=profile.label,
            wallet_balance=capital,
            available_balance=capital,
            unrealized_pnl=0.0,
            weighted_leverage=None,
            win_rate=None,
            profit_factor=None,
            realized_pnl=0.0,
            commission=0.0,
            funding=0.0,
            position_count=0,
            open_order_count=0,
            positions=pd.DataFrame(),
            open_orders=pd.DataFrame(),
            trades=pd.DataFrame(),
            income=pd.DataFrame(),
            strategy_stats=pd.DataFrame(),
        )

    df = bot_orders.copy()
    df["symbol"] = df["symbol"].astype(str).str.upper()
    df["side"] = df["side"].astype(str)
    df["entry"] = pd.to_numeric(df.get("entry"), errors="coerce")
    df["quantity"] = pd.to_numeric(df.get("quantity"), errors="coerce").fillna(0)
    df["leverage"] = pd.to_numeric(df.get("leverage"), errors="coerce").fillna(1)

    pos_records: list[dict[str, Any]] = []
    for (symbol, side), grp in df.groupby(["symbol", "side"]):
        qty = grp["quantity"].sum()
        if qty <= 0:
            continue
        entry = grp["entry"].astype(float)
        w = grp["quantity"].astype(float)
        avg_entry = float((entry * w).sum() / w.sum()) if w.sum() > 0 else 0.0
        lev = float(grp["leverage"].mean())
        strat = ""
        if "strategy_name" in grp.columns:
            named = grp["strategy_name"].dropna().astype(str)
            strat = named.iloc[-1] if len(named) else ""
        if not strat and "strategy_id" in grp.columns:
            strat = _strategy_name(str(grp["strategy_id"].iloc[-1]))
        open_time = ""
        if "created_at" in grp.columns:
            open_time = str(grp["created_at"].min())
        pos_records.append(
            {
                "symbol": symbol,
                "side": side,
                "size": qty,
                "entry_price": avg_entry,
                "mark_price": avg_entry,
                "unrealized_pnl": 0.0,
                "leverage": int(round(lev)),
                "strategy_name": strat,
                "open_time": open_time,
                "margin_type": grp["margin_type"].iloc[0] if "margin_type" in grp.columns else "cross",
                "liquidation_price": 0.0,
            }
        )
    positions = pd.DataFrame(pos_records)

    order_records: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        stop = _float(row.get("stop"), 0)
        tp = _float(row.get("take_profit"), 0)
        lev = int(_float(row.get("leverage"), 1))
        entry_px = _float(row.get("entry"))
        open_ts = str(row.get("created_at", "") or "")
        if stop > 0:
            order_records.append(
                {
                    "symbol": row["symbol"],
                    "type": "STOP_MARKET",
                    "side": "SELL" if row["side"] == "long" else "BUY",
                    "price": 0.0,
                    "stop_price": stop,
                    "quantity": _float(row.get("quantity")),
                    "filled": 0.0,
                    "reduce_only": True,
                    "status": "NEW",
                    "order_id": "",
                    "time": open_ts,
                    "open_time": open_ts,
                    "entry_price": entry_px,
                    "leverage": lev,
                }
            )
        if tp > 0:
            order_records.append(
                {
                    "symbol": row["symbol"],
                    "type": "TAKE_PROFIT_MARKET",
                    "side": "SELL" if row["side"] == "long" else "BUY",
                    "price": 0.0,
                    "stop_price": tp,
                    "quantity": _float(row.get("quantity")),
                    "filled": 0.0,
                    "reduce_only": True,
                    "status": "NEW",
                    "order_id": "",
                    "time": open_ts,
                    "open_time": open_ts,
                    "entry_price": entry_px,
                    "leverage": lev,
                }
            )
    open_orders = pd.DataFrame(order_records)

    trade_records: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        trade_records.append(
            {
                "time": row.get("created_at", ""),
                "symbol": row["symbol"],
                "side": "BUY" if row["side"] == "long" else "SELL",
                "direction": row["side"],
                "avg_price": _float(row.get("entry")),
                "quantity": _float(row.get("quantity")),
                "quote_qty": _float(row.get("entry")) * _float(row.get("quantity")),
                "realized_pnl": _float(row.get("realized_pnl")),
                "commission": 0.0,
                "commission_asset": "USDT",
                "order_id": row.get("exchange_order_id", ""),
                "trade_count": 1,
                "leverage": int(_float(row.get("leverage"), 1)),
                "strategy_name": row.get("strategy_name", row.get("strategy_id", "")),
            }
        )
    trades = pd.DataFrame(trade_records)
    if not trades.empty:
        trades = trades.sort_values("time", ascending=False).reset_index(drop=True)

    win_rate, pf = compute_trade_stats(trades)
    strategy_stats = merge_strategy_performance(trades, positions)

    return AccountView(
        mode=ExecMode.PAPER.value,
        account_id=profile.account_id,
        account_label=profile.label,
        wallet_balance=capital,
        available_balance=capital,
        unrealized_pnl=0.0,
        weighted_leverage=weighted_position_leverage(positions),
        win_rate=win_rate,
        profit_factor=pf,
        realized_pnl=0.0,
        commission=0.0,
        funding=0.0,
        position_count=len(positions),
        open_order_count=len(open_orders),
        positions=positions,
        open_orders=open_orders,
        trades=trades,
        income=pd.DataFrame(),
        strategy_stats=strategy_stats,
    )
