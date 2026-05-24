"""
量化交易 Streamlit 儀表板 — 主工作站 + 回測覆盤 + 模擬成交

啟動：
  cd trading-bot
  source .venv/bin/activate
  pip install -r requirements.txt
  streamlit run streamlit_app.py
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

# Streamlit Cloud（Linux）預設 locale 有時非 UTF-8，避免中文讀寫異常
os.environ.setdefault("LANG", "C.UTF-8")
os.environ.setdefault("LC_ALL", "C.UTF-8")
os.environ.setdefault("PYTHONUTF8", "1")

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from core.app_auth import auth_is_enabled, render_login_gate, render_logout_control
from core.backtest_report import run_backtest
from core.lightweight_tv import (
    build_lightweight_chart_html,
    build_order_panel_live_price_html,
    df_to_tv_series,
    markers_for_open_orders,
    markers_for_strategies,
)
from core.market_data import MarketType, fetch_klines, fetch_symbol_last_price, pop_source_note
from core.order_executor import (
    OrderMode,
    OrderRequest,
    list_paper_orders,
    place_paper_order,
    scan_and_paper_trade,
)
from core.strategy_registry import STRATEGIES, scan_signals_for
from core.universe import top_usdt_pairs_by_volume, universe_price_source_label

REPORTS_DIR = Path(__file__).parent / "data" / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

st.set_page_config(
    page_title="量化交易機器人",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# 雲端容器若無 CJK 字體，中文可能顯示為方塊或異常符號
_CJK_FONT_CSS = """
<style>
  html, body, [class*="st-"] {
    font-family: "Noto Sans CJK TC", "Noto Sans TC", "PingFang TC",
      "Microsoft JhengHei", "Heiti TC", sans-serif !important;
  }
</style>
"""
st.markdown(_CJK_FONT_CSS, unsafe_allow_html=True)


def _init_state() -> None:
    defaults = {
        "selected_symbol": "BTCUSDT",
        "selected_pair": "BTC/USDT",
        "market": "futures",
        "paper_enabled": True,
        "chart_highlight_id": "ema",
        "active_strategy_ids": list(STRATEGIES.keys()),
        "sidebar_top_n": 100,
        "sidebar_kline_limit": 500,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

    # 清掉已廢棄的 header 報告選單 state（曾導致整頁異常）
    for key in ("header_nav", "report_filter_sym", "report_filter_strat", "report_filter_status"):
        st.session_state.pop(key, None)

    # 舊版 Top100 選取 state 缺 cells 會導致啟動崩潰，一律清除
    top100 = st.session_state.get("sidebar_top100_select")
    if isinstance(top100, dict):
        sel = top100.get("selection")
        if not isinstance(sel, dict) or "cells" not in sel:
            st.session_state.pop("sidebar_top100_select", None)


@st.cache_data(ttl=3600)
def _cached_universe(top_n: int, market: str) -> pd.DataFrame:
    return top_usdt_pairs_by_volume(top_n=top_n, market=market)  # type: ignore[arg-type]


@st.cache_data(ttl=300)
def _cached_klines(
    symbol: str, interval: str, limit: int, market: str
) -> tuple[pd.DataFrame, str]:
    """回傳 (K 線 DataFrame, 實際來源 futures|spot|...)。"""
    df = fetch_klines(symbol, interval=interval, limit=limit, market=market)  # type: ignore[arg-type]
    src = str(df.attrs.get("price_source", market))
    return df, src


def _last_price(sym: str, universe_df: pd.DataFrame, market: MarketType) -> float:
    """靜態 fallback（榜單快取或 K 線收盤）。"""
    if not universe_df.empty and "symbol" in universe_df.columns:
        row = universe_df[universe_df["symbol"] == sym]
        if not row.empty and "last_price" in row.columns:
            return float(row.iloc[0]["last_price"])
    try:
        raw, _ = _cached_klines(sym, "1m", 2, market)
        return float(raw.iloc[-1]["close"])
    except Exception:
        return 0.0


def _dataframe_row_selection(row_index: int) -> dict:
    """Streamlit 1.57+ 列選取 state 須含 rows / columns / cells。"""
    return {
        "selection": {
            "rows": [row_index],
            "columns": [],
            "cells": [],
        }
    }


def _row_index_for_symbol(df: pd.DataFrame, symbol: str) -> int:
    """在榜單 DataFrame 中找 symbol 的列位置（供 dataframe 列選取）。"""
    if df.empty or "symbol" not in df.columns:
        return 0
    sym_u = symbol.replace("/", "").upper()
    for i, s in enumerate(df["symbol"].astype(str)):
        if s.upper() == sym_u:
            return i
    return 0


def _apply_top100_table_selection(universe_df: pd.DataFrame) -> None:
    """側欄 Top 100 點列 → 更新目前圖表幣種。"""
    if universe_df.empty:
        return
    raw = st.session_state.get("sidebar_top100_select")
    if raw is None:
        return
    sel = raw.get("selection") if isinstance(raw, dict) else getattr(raw, "selection", None)
    if not sel:
        return
    rows = sel.get("rows", []) if isinstance(sel, dict) else getattr(sel, "rows", [])
    if not rows:
        return
    idx = int(rows[0])
    if idx < 0 or idx >= len(universe_df):
        return
    row = universe_df.iloc[idx]
    new_sym = str(row["symbol"])
    if new_sym != st.session_state.get("selected_symbol"):
        st.session_state.selected_symbol = new_sym
        st.session_state.selected_pair = str(row["pair"])
        st.session_state["_symbol_pick_source"] = "sidebar"


def _sync_top100_table_highlight(universe_df: pd.DataFrame) -> None:
    """頂部選幣變更時，同步側欄表格的選取列。"""
    if universe_df.empty or st.session_state.get("_symbol_pick_source") != "toolbar":
        return
    row_i = _row_index_for_symbol(universe_df, st.session_state.selected_symbol)
    st.session_state["sidebar_top100_select"] = _dataframe_row_selection(row_i)


def _strategy_order_hints(
    sym: str, sid: str, market: MarketType
) -> dict[str, float | str] | None:
    meta = STRATEGIES.get(sid)
    if meta is None:
        return None
    raw, _ = _cached_klines(sym, meta.timeframe, 500, market)
    prep = meta.prepare_df(raw)
    sigs = scan_signals_for(sid, prep)
    if not sigs:
        return None
    last = sigs[-1]
    plan = last.plan
    tp = getattr(plan, "tp_final", None)
    return {
        "side": last.side,
        "entry": float(plan.entry),
        "stop": float(plan.stop),
        "take_profit": float(tp) if tp and float(tp) > 0 else 0.0,
        "quantity": float(getattr(plan, "position_size", 1.0) or 1.0),
    }


def _sidebar_panel(market: MarketType) -> tuple[int, int, pd.DataFrame]:
    """側欄由上而下：Top 100 表 → 成交量滑桿 → K 線根數滑桿。"""
    render_logout_control()
    st.sidebar.markdown("### 📊 Top 100")

    top_n_cur = int(st.session_state.get("sidebar_top_n", 100))
    universe_df = _cached_universe(top_n_cur, market)

    if st.sidebar.button("重新抓取榜單", key="refresh_universe_sidebar"):
        from core.universe import _cache_path

        p = _cache_path(market, top_n_cur)
        if p.exists():
            p.unlink()
        st.cache_data.clear()
        st.rerun()

    if universe_df.empty:
        st.sidebar.warning("榜單載入中或失敗")
    else:
        display_df = universe_df[["rank", "pair", "quote_volume_24h", "price_change_pct"]]
        _sync_top100_table_highlight(universe_df)
        default_row = _row_index_for_symbol(universe_df, st.session_state.selected_symbol)
        st.sidebar.dataframe(
            display_df,
            use_container_width=True,
            height=380,
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
            key="sidebar_top100_select",
            selection_default=_dataframe_row_selection(default_row),
        )
        _apply_top100_table_selection(universe_df)
        src_lbl = universe_price_source_label(universe_df)
        st.sidebar.caption(
            f"共 {len(universe_df)} 檔 · {'永續' if market == 'futures' else '現貨'} · "
            f"榜單行情：{src_lbl} · 點選列切換 K 線"
        )
        if market == "futures" and src_lbl != "永續 (fapi)":
            st.sidebar.warning(
                "目前 Top 100 並非永續 fapi 報價。"
                "亞洲主機可在 Zeabur 變數設 BINANCE_STRICT_FUTURES=1 強制僅用永續；"
                "或點「重新抓取榜單」。",
                icon="⚠️",
            )

    top_n = st.sidebar.slider(
        "成交量 Top N",
        20,
        100,
        top_n_cur,
        step=10,
        key="sidebar_top_n",
    )
    kline_limit = st.sidebar.slider(
        "K 線根數",
        200,
        1000,
        int(st.session_state.get("sidebar_kline_limit", 500)),
        step=100,
        key="sidebar_kline_limit",
    )
    st.sidebar.caption("回測 → 模擬 → 實盤（需 API + 手動確認）")

    universe_df = _cached_universe(top_n, market)
    return kline_limit, top_n, universe_df


def _top_toolbar(universe_df: pd.DataFrame) -> tuple[list[str], str, str, MarketType, str, bool]:
    """頂部：策略多選、圖表高亮、幣種、市場、K 週期、WS。"""
    all_ids = list(STRATEGIES.keys())
    if not st.session_state.active_strategy_ids:
        st.session_state.active_strategy_ids = all_ids

    c1, c6, c2, c3, c5, c7 = st.columns([2.4, 0.9, 1.3, 1.6, 1.1, 0.7])

    with c1:
        strategy_ids = st.multiselect(
            "啟用策略（多選）",
            options=all_ids,
            default=[s for s in st.session_state.active_strategy_ids if s in all_ids] or all_ids,
            format_func=lambda x: STRATEGIES[x].name,
            key="toolbar_strategies",
        )
        st.session_state.active_strategy_ids = strategy_ids or all_ids

    tf_options = ["1m", "5m", "15m", "1h", "4h", "1d"]
    with c6:
        chart_tf = st.selectbox(
            "K 線週期",
            tf_options,
            index=tf_options.index("5m") if "5m" in tf_options else 1,
            key="toolbar_chart_tf",
        )

    highlight_pool = [s for s in strategy_ids if STRATEGIES[s].timeframe == chart_tf]
    if not highlight_pool:
        highlight_pool = strategy_ids or all_ids

    cur_hi = st.session_state.chart_highlight_id
    hi_index = highlight_pool.index(cur_hi) if cur_hi in highlight_pool else 0

    with c2:
        chart_highlight = st.selectbox(
            "圖表高亮",
            highlight_pool,
            index=hi_index,
            format_func=lambda x: STRATEGIES[x].name,
            key="toolbar_chart_highlight",
        )
        st.session_state.chart_highlight_id = chart_highlight

    symbols: list[str] = []
    labels: list[str] = []
    label_to_sym: dict[str, str] = {}
    if not universe_df.empty:
        symbols = universe_df["symbol"].tolist()
        labels = [
            f"{int(r['rank']):>3}  {r['pair']}"
            for _, r in universe_df.iterrows()
        ]
        label_to_sym = dict(zip(labels, symbols))

    with c3:
        if labels:
            cur = st.session_state.selected_symbol
            default_i = symbols.index(cur) if cur in symbols else 0
            choice = st.selectbox("選取幣種", labels, index=default_i, key="toolbar_pair")
            sym = label_to_sym[choice]
            prev_sym = st.session_state.get("selected_symbol")
            st.session_state.selected_symbol = sym
            row = universe_df[universe_df["symbol"] == sym].iloc[0]
            st.session_state.selected_pair = row["pair"]
            if sym != prev_sym:
                st.session_state["_symbol_pick_source"] = "toolbar"
        else:
            sym = st.session_state.selected_symbol

    with c5:
        market: MarketType = st.selectbox(
            "市場",
            options=["futures", "spot"],
            format_func=lambda x: "永續" if x == "futures" else "現貨",
            index=0 if st.session_state.market == "futures" else 1,
            key="toolbar_market",
        )
        st.session_state.market = market

    with c7:
        use_live = st.checkbox("WS 即時", value=True, key="toolbar_use_ws")

    sym = st.session_state.selected_symbol
    return strategy_ids, chart_highlight, sym, market, chart_tf, use_live


def _signal_chips(
    raw: pd.DataFrame,
    strategy_ids: list[str],
    chart_tf: str,
    chart_highlight: str,
) -> None:
    """條件觸發狀態：多策略摘要，高亮策略單獨標示。"""
    sig_ids = [s for s in strategy_ids if STRATEGIES[s].timeframe == chart_tf]
    if not sig_ids:
        st.warning(f"啟用策略中無週期 **{chart_tf}** 者，圖上僅顯示 K 線。")
        return

    chips: list[str] = []
    for sid in sig_ids:
        prep = STRATEGIES[sid].prepare_df(raw)
        sigs = scan_signals_for(sid, prep)
        name = STRATEGIES[sid].name
        if not sigs:
            chips.append(f"⚪ {name}：本區間無訊號")
            continue
        last = sigs[-1]
        on_last = last.bar_index >= len(prep) - 2
        side = last.side.upper()
        trigger = "🟢 觸發" if on_last else "訊號"
        prefix = "⭐" if sid == chart_highlight else "▫️"
        chips.append(
            f"{prefix} **{name}** · {trigger} · {side} · 進場 {last.plan.entry:.6g} · "
            f"止損 {last.plan.stop:.6g} · K#{last.bar_index}"
        )

    st.markdown(" · ".join(chips))


def _order_right_panel(
    strategy_ids: list[str],
    market: MarketType,
    universe_df: pd.DataFrame,
    chart_highlight: str,
    use_live: bool,
) -> None:
    sym = st.session_state.selected_symbol
    pair = st.session_state.selected_pair
    last_px = _last_price(sym, universe_df, market)

    st.markdown("##### 下單")
    st.caption(pair)
    if use_live:
        components.html(
            build_order_panel_live_price_html(sym, market),
            height=88,
            scrolling=False,
        )
    elif last_px > 0:
        st.metric("最新價", f"{last_px:,.6g}")
        st.caption("離線／未啟用 WS · 榜單參考價")
    else:
        st.metric("最新價", "—")

    st.session_state.paper_enabled = st.toggle(
        "啟用模擬開單",
        value=st.session_state.paper_enabled,
        key="order_paper_toggle",
    )

    order_style = st.radio(
        "方式",
        ["手動開倉", "自動掃描"],
        horizontal=True,
        key="order_style",
    )
    exec_mode = st.radio(
        "帳戶",
        ["paper", "live"],
        format_func=lambda x: "模擬" if x == "paper" else "實盤",
        horizontal=True,
        key="order_exec_mode",
    )

    sid_pool = strategy_ids or [chart_highlight]
    sid = st.selectbox(
        "策略（帶入參考）",
        sid_pool,
        format_func=lambda x: STRATEGIES[x].name,
        key="order_form_sid",
    )

    if order_style == "自動掃描":
        scan_n = st.slider("掃描榜單前 N", 5, 30, 10, key="order_scan_n")
        if st.button("掃描 → 模擬下單", type="primary", key="order_scan_btn"):
            if not st.session_state.paper_enabled:
                st.warning("請先開啟「啟用模擬開單」")
            elif exec_mode != "paper":
                st.warning("自動掃描目前僅支援模擬盤")
            elif not strategy_ids:
                st.warning("請在頂部至少選一個策略")
            else:
                syms = universe_df["symbol"].head(scan_n).tolist()
                with st.spinner("掃描中…"):
                    placed = scan_and_paper_trade(syms, strategy_ids, market=market)
                st.success(f"本輪模擬成交 {len(placed)} 筆")
                st.rerun()
    else:
        hints = _strategy_order_hints(sym, sid, market)
        default_entry = float(hints["entry"]) if hints else last_px
        default_stop = float(hints["stop"]) if hints else (default_entry * 0.98 if default_entry else 0.0)
        default_tp = float(hints["take_profit"]) if hints and hints["take_profit"] else 0.0
        default_qty = float(hints["quantity"]) if hints else 0.01
        default_side = hints["side"] if hints else "long"

        fill_side = default_side
        if st.button("從策略帶入價格", key="order_fill_from_strategy"):
            fill_side = str(hints["side"]) if hints else default_side
            st.session_state["order_price_input"] = float(hints["entry"]) if hints else default_entry
            st.session_state["order_stop_input"] = float(hints["stop"]) if hints else default_stop
            st.session_state["order_tp_input"] = float(hints["take_profit"]) if hints and hints["take_profit"] else default_tp
            st.session_state["order_qty_input"] = float(hints["quantity"]) if hints else default_qty
            st.session_state["order_side_pick"] = fill_side
            st.rerun()

        if "order_side_pick" not in st.session_state:
            st.session_state["order_side_pick"] = default_side
        side_label = st.radio(
            "方向",
            ["做多", "做空"],
            index=0 if st.session_state["order_side_pick"] == "long" else 1,
            horizontal=True,
            key="order_side_radio",
        )
        st.session_state["order_side_pick"] = "long" if side_label == "做多" else "short"

        order_type_label = st.selectbox(
            "委託類型",
            ["市價", "限價"],
            key="order_type_select",
        )
        order_type = "market" if order_type_label == "市價" else "limit"

        price = st.number_input(
            "價格",
            min_value=0.0,
            value=float(default_entry) or last_px or 0.0,
            format="%.8f",
            disabled=order_type == "market",
            key="order_price_input",
        )
        if order_type == "market":
            st.caption("市價單以最新價成交（模擬記錄用目前最新價）")
            fill_px = fetch_symbol_last_price(sym, market)
            if fill_px <= 0:
                fill_px = last_px
            price = fill_px if fill_px > 0 else float(st.session_state.get("order_price_input", price))

        qty = st.number_input(
            "數量（幣）",
            min_value=0.0,
            value=float(default_qty),
            format="%.6f",
            key="order_qty_input",
        )

        if market == "futures":
            leverage = st.select_slider(
                "槓桿倍數",
                options=[1, 2, 3, 5, 10, 20, 25, 50, 75, 100, 125],
                value=10,
                key="order_leverage",
            )
            margin_type = st.selectbox(
                "保證金",
                ["全倉", "逐倉"],
                key="order_margin",
            )
            margin_val = "cross" if margin_type == "全倉" else "isolated"
        else:
            leverage = 1
            margin_val = "cross"
            st.caption("現貨無槓桿，倍數固定 1x")

        stop_px = st.number_input(
            "止損價格",
            min_value=0.0,
            value=float(default_stop),
            format="%.8f",
            key="order_stop_input",
        )
        tp_px = st.number_input(
            "止盈價格",
            min_value=0.0,
            value=float(default_tp),
            format="%.8f",
            key="order_tp_input",
        )

        notional = price * qty * leverage if price and qty else 0.0
        st.caption(f"名義價值 ≈ {notional:,.4f} USDT（價格 × 數量 × 槓桿）")

        if st.button("開倉", type="primary", key="order_submit_btn"):
            if not st.session_state.paper_enabled:
                st.warning("請先開啟「啟用模擬開單」")
            elif exec_mode != "paper":
                st.warning("表單開倉目前僅支援模擬盤")
            elif price <= 0 or qty <= 0:
                st.warning("請填寫有效價格與數量")
            elif stop_px <= 0:
                st.warning("請填寫止損價格")
            else:
                entry = price
                side = st.session_state.get("order_side_pick", "long")
                req = OrderRequest(
                    symbol=sym,
                    strategy_id=sid,
                    side=side,
                    entry=entry,
                    stop=stop_px,
                    quantity=qty,
                    mode=OrderMode.PAPER,
                    order_type=order_type,
                    price=price if order_type == "limit" else entry,
                    leverage=int(leverage),
                    take_profit=tp_px if tp_px > 0 else None,
                    margin_type=margin_val,
                )
                place_paper_order(req)
                st.success("已寫入模擬單")
                st.rerun()

    if exec_mode == "live":
        st.warning("實盤需 API；建議先用 Testnet。")
        if st.checkbox("我了解風險", key="order_live_confirm"):
            st.caption("實盤按鈕尚未啟用，請完成 testnet 後再接。")

    st.markdown("---")
    st.markdown("##### 本幣持倉 / 紀錄")
    orders = list_paper_orders()
    if orders.empty:
        st.info("尚無模擬單")
        return
    sym_u = sym.replace("/", "").upper()
    if "symbol" in orders.columns:
        here = orders[orders["symbol"].astype(str).str.upper() == sym_u]
    else:
        here = orders
    show = here if not here.empty else orders.tail(8)
    cols_pref = [
        c
        for c in [
            "side",
            "order_type",
            "entry",
            "price",
            "stop",
            "take_profit",
            "quantity",
            "leverage",
            "margin_type",
            "strategy_id",
            "status",
            "created_at",
        ]
        if c in show.columns
    ]
    st.dataframe(show[cols_pref] if cols_pref else show, use_container_width=True, height=240)


def _main_workstation(
    strategy_ids: list[str],
    chart_highlight: str,
    sym: str,
    market: MarketType,
    chart_tf: str,
    use_live: bool,
    kline_limit: int,
    universe_df: pd.DataFrame,
) -> None:
    pair = st.session_state.selected_pair
    hi_name = STRATEGIES[chart_highlight].name

    if st.button("重新載入歷史 K 線", key="refresh_klines_main"):
        st.cache_data.clear()
        st.rerun()

    col_chart, col_orders = st.columns([2.55, 1.15], gap="medium")

    with col_orders:
        _order_right_panel(strategy_ids, market, universe_df, chart_highlight, use_live)

    with col_chart:
        try:
            raw, k_src = _cached_klines(sym, chart_tf, kline_limit, market)
            if market == "futures" and k_src and k_src != "futures":
                st.warning(
                    f"歷史 K 線來源為「{k_src}」，非永續 fapi；"
                    "與合約即時價可能不一致。",
                    icon="⚠️",
                )
            candles, volumes = df_to_tv_series(raw)
            markers: list[dict] = []
            if STRATEGIES[chart_highlight].timeframe == chart_tf:
                prep_hi = STRATEGIES[chart_highlight].prepare_df(raw)
                markers = markers_for_strategies(prep_hi, [chart_highlight])
            orders_df = list_paper_orders()
            markers.extend(markers_for_open_orders(orders_df, sym, candles))

            mkt_label = "永續" if market == "futures" else "現貨"
            ws_label = "WS" if use_live else "離線"
            title = f"{pair} · {chart_tf} · 高亮 {hi_name} · {mkt_label} · {ws_label}"

            html_doc = build_lightweight_chart_html(
                candles=candles,
                volumes=volumes,
                markers=markers,
                title=title,
                symbol=sym,
                chart_interval=chart_tf,
                market=market,
                use_live=use_live,
                chart_height=560,
            )
            components.html(html_doc, height=640, scrolling=False)

            _signal_chips(raw, strategy_ids, chart_tf, chart_highlight)

            if STRATEGIES[chart_highlight].timeframe != chart_tf:
                st.caption(
                    f"圖表週期 {chart_tf} 與高亮策略週期 {STRATEGIES[chart_highlight].timeframe} 不同，"
                    "箭頭仍依圖表週期資料計算（可能與策略預期週期不一致）。"
                )

        except Exception as exc:
            _show_binance_source_banner()
            st.error(
                f"無法載入圖表：{exc}\n\n"
                "Streamlit Cloud 伺服器若無法連幣安，請在頂部改選「現貨」或稍後重試；"
                "即時 WebSocket 仍由你的瀏覽器直連幣安。"
            )


def _tab_backtest(strategy_ids: list[str], market: MarketType, kline_limit: int) -> None:
    """5/20 初版：策略回測覆盤（英文欄位 DataFrame，避免編碼問題）。"""
    st.subheader("策略回測覆盤")
    sym = st.session_state.selected_symbol
    pair = st.session_state.selected_pair
    st.caption(f"目前交易對：{pair}（{sym}）· 策略來自主工作站側欄／頂部多選")

    if st.button("執行回測（目前交易對 × 已選策略）", type="primary", key="tab_bt_run"):
        if not strategy_ids:
            st.warning("請在主工作站頂部至少選一個策略")
            return
        with st.spinner("回測中…"):
            try:
                rows = []
                events_map: dict[str, list[str]] = {}
                for sid in strategy_ids:
                    meta = STRATEGIES[sid]
                    raw_s, _ = _cached_klines(sym, meta.timeframe, kline_limit, market)
                    r = run_backtest(sid, pair, raw_s)
                    rows.append(r.to_dict())
                    events_map[sid] = r.events
                st.session_state["last_backtest"] = pd.DataFrame(rows)
                st.session_state["last_backtest_events"] = events_map
            except Exception as exc:
                st.error(str(exc))
                return

    if "last_backtest" not in st.session_state:
        st.info("按上方按鈕執行回測後，此處會顯示各策略勝率與成交統計。")
        return

    df = st.session_state["last_backtest"]
    st.dataframe(df, use_container_width=True)
    avg_wr = df["win_rate_pct"].mean() if len(df) and "win_rate_pct" in df.columns else 0
    st.metric("平均勝率（已平倉事件估算）", f"{avg_wr:.1f}%")

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = REPORTS_DIR / f"backtest_{sym}_{ts}.json"
    payload = {
        "symbol": sym,
        "pair": pair,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "results": df.to_dict(orient="records"),
    }
    if st.button("儲存覆盤報告 JSON", key="tab_bt_save_json"):
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        st.success(f"已儲存：{out_path}")

    with st.expander("引擎事件日誌（最後一個策略範例）"):
        if strategy_ids:
            ev = st.session_state.get("last_backtest_events", {}).get(strategy_ids[-1], [])
            st.code("\n".join(ev[-40:]) if ev else "無")


def _tab_paper_fills() -> None:
    """5/20 初版：模擬成交紀錄（原始 JSON 欄位直接顯示）。"""
    st.subheader("模擬成交紀錄")
    st.caption("資料來源：data/paper_orders.json · 下單請至「主工作站」右欄")

    orders = list_paper_orders(limit=200)
    if orders.empty:
        st.info("尚無模擬單")
        return

    show = orders.copy()
    if "strategy_id" in show.columns:
        show["strategy_name"] = show["strategy_id"].map(
            lambda x: STRATEGIES[x].name if x in STRATEGIES else str(x)
        )
    st.dataframe(show, use_container_width=True, height=520)


def _show_binance_source_banner() -> None:
    note = pop_source_note()
    if note:
        st.warning(note, icon="⚠️")


def main() -> None:
    if not render_login_gate():
        st.stop()
    _init_state()
    if not auth_is_enabled():
        st.sidebar.warning(
            "未設定 APP_LOGIN_PASSWORD，站台未啟用登入保護。"
            "正式環境請於 Zeabur Variables 或本機 .env 設定。",
            icon="⚠️",
        )
    st.title("📈 量化交易工作站")

    market: MarketType = st.session_state.market
    kline_limit, top_n, universe_df = _sidebar_panel(market)

    tab_main, tab_bt, tab_fills = st.tabs(["主工作站", "回測覆盤", "模擬成交"])

    with tab_main:
        st.caption("頂部多選策略監控 · 圖表僅高亮單一策略 · 右欄下單 · 側欄 Top 榜")
        strategy_ids, chart_highlight, sym, market, chart_tf, use_live = _top_toolbar(universe_df)
        st.session_state.market = market
        st.markdown("---")
        _main_workstation(
            strategy_ids,
            chart_highlight,
            sym,
            market,
            chart_tf,
            use_live,
            kline_limit,
            universe_df,
        )
        _show_binance_source_banner()

    with tab_bt:
        strategy_ids = st.session_state.get("active_strategy_ids") or list(STRATEGIES.keys())
        _tab_backtest(strategy_ids, market, kline_limit)

    with tab_fills:
        _tab_paper_fills()


if __name__ == "__main__":
    main()
