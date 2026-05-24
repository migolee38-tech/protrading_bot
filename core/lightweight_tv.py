"""TradingView 風格圖：Lightweight Charts + 瀏覽端 Binance WebSocket 即時 K 線。

初始 K 線與策略 markers 由 Python 注入 JSON；連線後以 WS 增量更新。
啟用 @aggTrade 時：開盤價與成交量僅跟 @kline_；high/low/close 可隨每笔聚合成交 refinement（仍以 kline 同步整根快照）。
需可連線 unpkg CDN（載入 lightweight-charts@4）。
"""

from __future__ import annotations

import html
import json
from typing import Any

import pandas as pd

from core.market_data import MarketType


def binance_kline_ws_url(symbol: str, interval: str, market: MarketType) -> str:
    """單一流 @kline_ 串流網址（小寫 symbol）。"""
    s = symbol.replace("/", "").lower()
    interval = interval.strip().lower()
    if market == "futures":
        return f"wss://fstream.binance.com/ws/{s}@kline_{interval}"
    return f"wss://stream.binance.com:9443/ws/{s}@kline_{interval}"


def binance_mark_price_ws_url(symbol: str, market: MarketType) -> str:
    """USDT 永續標記價，每秒推送（與 K 線分立第二條 WS）。"""
    if market != "futures":
        return ""
    s = symbol.replace("/", "").lower()
    return f"wss://fstream.binance.com/ws/{s}@markPrice@1s"


def binance_agg_trade_ws_url(symbol: str, market: MarketType) -> str:
    """聚合成交 @aggTrade（現貨 / 永續）— 高頻更新，用於最新價與圖上 Last 線。"""
    s = symbol.replace("/", "").lower()
    if market == "futures":
        return f"wss://fstream.binance.com/ws/{s}@aggTrade"
    return f"wss://stream.binance.com:9443/ws/{s}@aggTrade"


def _time_sec(ts: Any) -> int:
    if hasattr(ts, "timestamp"):
        return int(ts.timestamp())
    return int(pd.Timestamp(ts).timestamp())


def df_to_tv_series(df: pd.DataFrame) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """輕量圖表用 candlestick + histogram volumes。"""
    candles: list[dict[str, Any]] = []
    volumes: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        t = _time_sec(row["datetime"])
        o, h, lo, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
        candles.append({"time": t, "open": o, "high": h, "low": lo, "close": c})
        col = "#26a69a" if c >= o else "#ef5350"
        volumes.append({"time": t, "value": float(row["volume"]), "color": col})
    return candles, volumes


def markers_for_strategies(prep_for_tf: pd.DataFrame, strategy_ids: list[str]) -> list[dict[str, Any]]:
    """
    從已對齊週期的 prep DataFrame 產生 lightweight-charts candlestick markers。
    """
    from core.strategy_registry import STRATEGIES, scan_signals_for

    xs = prep_for_tf["datetime"]
    out: list[dict[str, Any]] = []

    strat_labels = {
        "ema": "E",
        "donchian": "D",
        "rsi": "R",
        "macd": "M",
    }

    for sid in strategy_ids:
        meta = STRATEGIES.get(sid)
        if meta is None:
            continue
        sigs = scan_signals_for(sid, prep_for_tf)
        tag = strat_labels.get(sid, sid[:3].upper())
        for sig in sigs:
            idx = getattr(sig, "bar_index", 0)
            if idx >= len(xs) or idx < 0:
                continue
            t = _time_sec(xs.iloc[idx])
            is_long = sig.side == "long"
            out.append(
                {
                    "time": t,
                    "position": "belowBar" if is_long else "aboveBar",
                    "color": "#26a69a" if is_long else "#ef5350",
                    "shape": "arrowUp" if is_long else "arrowDown",
                    "text": tag,
                }
            )

    out.sort(key=lambda m: m["time"])
    return out


def markers_for_open_orders(
    orders: pd.DataFrame,
    symbol: str,
    candles: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """將模擬／實盤未平倉單標在 K 線上（依 created_at 對齊最近 bar time）。"""
    if orders.empty or not candles:
        return []
    sym = symbol.replace("/", "").upper()
    if "symbol" not in orders.columns:
        return []
    sub = orders[orders["symbol"].astype(str).str.upper() == sym]
    if sub.empty:
        return []

    bar_times = sorted(int(c["time"]) for c in candles)
    out: list[dict[str, Any]] = []

    for _, row in sub.iterrows():
        created = row.get("created_at")
        if not created or (isinstance(created, float) and pd.isna(created)):
            continue
        try:
            ts = pd.Timestamp(created)
            if ts.tzinfo is None:
                ts = ts.tz_localize("UTC")
            order_sec = int(ts.timestamp())
        except (TypeError, ValueError):
            continue

        t_bar = bar_times[0]
        for bt in bar_times:
            if bt <= order_sec:
                t_bar = bt
            else:
                break

        side = str(row.get("side", "long")).lower()
        is_long = side == "long"
        out.append(
            {
                "time": t_bar,
                "position": "inBar",
                "color": "#ffb300" if is_long else "#ff7043",
                "shape": "circle",
                "text": "持",
            }
        )

    return out


def build_lightweight_chart_html(
    *,
    candles: list[dict[str, Any]],
    volumes: list[dict[str, Any]],
    markers: list[dict[str, Any]],
    ws_url: str,
    title: str,
    chart_height: int = 600,
    mark_price_ws_url: str = "",
    mark_price_note: str | None = None,
    agg_trade_ws_url: str = "",
) -> str:
    """產生可給 streamlit.components.v1.html 的完整 HTML 文件。"""
    boot = {
        "candles": candles,
        "volumes": volumes,
        "markers": markers,
        "wsUrl": ws_url,
        "markPriceWsUrl": mark_price_ws_url,
        "markPriceNote": mark_price_note,
        "aggTradeWsUrl": agg_trade_ws_url,
        "title": title,
        "chartHeight": chart_height,
    }
    boot_json = json.dumps(boot, ensure_ascii=False)
    title_esc = html.escape(title)

    # lightweight-charts v4 standalone（瀏覽器需能連 unpkg）
    cdn = "https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"/>
<style>
  html, body {{ margin: 0; padding: 0; background: #131722; overflow: hidden;
    font-family: "Noto Sans CJK TC", "Noto Sans TC", "PingFang TC", "Microsoft JhengHei", sans-serif; }}
  #wrap {{ width: 100%; height: 100vh; display: flex; flex-direction: column; }}
  #hdr {{ color: #d1d4dc; font-size: 12px; padding: 6px 8px 4px; border-bottom: 1px solid #2a2e39; }}
  #titleRow {{ font-weight: 600; margin-bottom: 4px; }}
  #markRow {{ display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }}
  .mark-label {{ color: #787b86; font-size: 11px; }}
  #markPx {{ font-size: 18px; font-weight: 700; font-variant-numeric: tabular-nums; letter-spacing: 0.02em; transition: color 0.08s; }}
  #markPx.up {{ color: #26a69a; }}
  #markPx.down {{ color: #ef5350; }}
  #markPx.flat {{ color: #f0b90b; }}
  #tradeRow {{ display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; margin-top: 4px; }}
  #tradePx {{ font-size: 17px; font-weight: 700; font-variant-numeric: tabular-nums; transition: color 0.06s; }}
  #tradePx.buy {{ color: #26a69a; }}
  #tradePx.sell {{ color: #ef5350; }}
  #tradePx.flat {{ color: #d1d4dc; }}
  #aggRate {{ font-size: 10px; color: #787b86; }}
  #chart {{ flex: 1; min-height: 0; width: 100%; }}
  #st {{ color: #787b86; font-size: 11px; padding: 4px 8px 6px; }}
</style>
<script src="{cdn}"></script>
</head>
<body>
<div id="wrap">
  <div id="hdr">
    <div id="titleRow">{title_esc}</div>
    <div id="markRow">
      <span class="mark-label">標記價 (Mark)</span>
      <span id="markPx" class="flat">—</span>
    </div>
    <div id="tradeRow">
      <span class="mark-label">最新成交 (aggTrade)</span>
      <span id="tradePx" class="flat">—</span>
      <span id="aggRate"></span>
    </div>
  </div>
  <div id="chart"></div>
  <div id="st">Live: WebSocket 連線中…</div>
</div>
<script>
(function() {{
  const BOOT = {boot_json};
  const el = document.getElementById('chart');
  const st = document.getElementById('st');
  const markPxEl = document.getElementById('markPx');
  const tradePxEl = document.getElementById('tradePx');
  const aggRateEl = document.getElementById('aggRate');

  const chart = LightweightCharts.createChart(el, {{
    width: el.clientWidth,
    height: Math.max(320, BOOT.chartHeight || 600),
    layout: {{
      background: {{ color: '#131722' }},
      textColor: '#d1d4dc',
    }},
    grid: {{
      vertLines: {{ color: '#2a2e39' }},
      horzLines: {{ color: '#2a2e39' }},
    }},
    crosshair: {{
      mode: LightweightCharts.CrosshairMode.Normal,
    }},
    timeScale: {{
      timeVisible: true,
      secondsVisible: false,
      borderColor: '#2a2e39',
    }},
    rightPriceScale: {{
      borderColor: '#2a2e39',
      scaleMargins: {{ top: 0.08, bottom: 0.22 }},
    }},
  }});

  const candleSeries = chart.addCandlestickSeries({{
    upColor: '#26a69a', downColor: '#ef5350',
    borderVisible: false,
    wickUpColor: '#26a69a', wickDownColor: '#ef5350',
  }});

  const volumeSeries = chart.addHistogramSeries({{
    color: '#26a69a',
    priceFormat: {{ type: 'volume' }},
    priceScaleId: '',
    scaleMargins: {{ top: 0.85, bottom: 0 }},
  }});

  let markSeries = null;
  let lastTradeSeries = null;
  if (BOOT.markPriceWsUrl) {{
    markSeries = chart.addLineSeries({{
      color: '#f0b90b',
      lineWidth: 2,
      priceLineVisible: true,
      lastValueVisible: true,
      title: 'Mark',
    }});
  }}
  if (BOOT.aggTradeWsUrl) {{
    lastTradeSeries = chart.addLineSeries({{
      color: '#29b6f6',
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: true,
      title: 'Last',
    }});
  }}
  if (!BOOT.markPriceWsUrl) {{
    markPxEl.style.fontSize = '12px';
    markPxEl.style.fontWeight = '500';
    markPxEl.className = 'flat';
    if (BOOT.markPriceNote === 'spot') {{
      markPxEl.textContent = '（現貨無標記價串流）';
    }} else if (BOOT.markPriceNote === 'offline') {{
      markPxEl.textContent = '（未啟用即時 WS · 無標記價）';
    }} else {{
      markPxEl.textContent = '—';
    }}
  }}

  candleSeries.setData(BOOT.candles);
  volumeSeries.setData(BOOT.volumes);
  if (BOOT.markers && BOOT.markers.length) {{
    candleSeries.setMarkers(BOOT.markers);
  }}

  let lastBarTime = null;
  /** 僅在未完成棒上 agg refinement：open 永遠跟 kline；high/low/close 可跟 agg */
  let currentBarOpen = null;
  let currentBarHigh = null;
  let currentBarLow = null;
  if (BOOT.candles && BOOT.candles.length) {{
    const last = BOOT.candles[BOOT.candles.length - 1];
    lastBarTime = last.time;
    if (BOOT.aggTradeWsUrl) {{
      currentBarOpen = last.open;
      currentBarHigh = last.high;
      currentBarLow = last.low;
    }}
  }}

  chart.timeScale().fitContent();

  function resize() {{
    chart.applyOptions({{ width: el.clientWidth, height: Math.max(320, BOOT.chartHeight || 600) }});
  }}
  window.addEventListener('resize', resize);
  new ResizeObserver(resize).observe(el.parentElement || el);

  let prevMark = null;

  function formatPx(p) {{
    const n = parseFloat(p);
    if (!isFinite(n)) return '—';
    const a = Math.abs(n);
    const d = a >= 1 ? 4 : a >= 0.01 ? 6 : 8;
    return n.toLocaleString('en-US', {{ minimumFractionDigits: 2, maximumFractionDigits: d }});
  }}

  function setMarkDisplay(p) {{
    const n = parseFloat(p);
    if (!isFinite(n)) return;
    markPxEl.textContent = formatPx(n);
    let cls = 'flat';
    if (prevMark != null) {{
      if (n > prevMark) cls = 'up';
      else if (n < prevMark) cls = 'down';
    }}
    markPxEl.className = cls;
    prevMark = n;
    if (markSeries && lastBarTime != null) {{
      markSeries.update({{ time: lastBarTime, value: n }});
    }}
  }}

  let klineOk = false;
  let markOk = false;
  let aggOk = false;
  function statusLine() {{
    const parts = [];
    if (BOOT.wsUrl) parts.push('K線 ' + (klineOk ? '✓' : '…'));
    if (BOOT.markPriceWsUrl) parts.push('標記價 ' + (markOk ? '✓' : '…'));
    if (BOOT.aggTradeWsUrl) parts.push('Agg ' + (aggOk ? '✓' : '…'));
    if (!BOOT.wsUrl && !BOOT.markPriceWsUrl && !BOOT.aggTradeWsUrl) {{
      st.textContent = '未設定 WebSocket（僅顯示歷史 K）';
      return;
    }}
    if (parts.length === 0) {{
      st.textContent = '未設定 WebSocket（僅顯示歷史 K）';
      return;
    }}
    st.textContent = 'Live: ' + parts.join('  ·  ');
    st.style.color = (klineOk || markOk || aggOk) ? '#26a69a' : '#787b86';
  }}

  /** 每秒統計進來的 agg 筆數（非畫面刷新次數） */
  let aggEventsThisSecond = 0;
  let aggStatTimer = null;
  function bumpAggCounter() {{
    aggEventsThisSecond += 1;
  }}
  function startAggRateTimer() {{
    if (aggStatTimer || !BOOT.aggTradeWsUrl) return;
    aggStatTimer = setInterval(() => {{
      if (BOOT.aggTradeWsUrl && aggOk) {{
        aggRateEl.textContent = aggEventsThisSecond
          ? '(' + aggEventsThisSecond + ' agg/s)'
          : '';
      }}
      aggEventsThisSecond = 0;
    }}, 1000);
  }}

  /** 聚合成交節流：同一幀合併多筆，只刷最新價 + Last 線 */
  let pendingTradePrice = null;
  let pendingTradeMaker = false;
  let rafPending = false;
  function flushAggUi() {{
    rafPending = false;
    if (pendingTradePrice == null || !isFinite(pendingTradePrice)) return;
    tradePxEl.textContent = formatPx(pendingTradePrice);
    tradePxEl.className = pendingTradeMaker ? 'sell' : 'buy';
    if (lastTradeSeries && lastBarTime != null) {{
      lastTradeSeries.update({{ time: lastBarTime, value: pendingTradePrice }});
    }}
    if (
      BOOT.aggTradeWsUrl &&
      currentBarOpen != null &&
      currentBarHigh != null &&
      currentBarLow != null &&
      lastBarTime != null
    ) {{
      const px = pendingTradePrice;
      currentBarHigh = Math.max(currentBarHigh, px);
      currentBarLow = Math.min(currentBarLow, px);
      candleSeries.update({{
        time: lastBarTime,
        open: currentBarOpen,
        high: currentBarHigh,
        low: currentBarLow,
        close: px,
      }});
    }}
  }}
  function queueAggTrade(price, mBuyerMaker) {{
    bumpAggCounter();
    pendingTradePrice = price;
    pendingTradeMaker = mBuyerMaker;
    if (!rafPending) {{
      rafPending = true;
      requestAnimationFrame(flushAggUi);
    }}
  }}

  let closed = false;

  function connectKline() {{
    if (!BOOT.wsUrl || closed) return;
    const ws = new WebSocket(BOOT.wsUrl);
    ws.onopen = () => {{ klineOk = true; statusLine(); }};
    ws.onclose = () => {{
      klineOk = false;
      statusLine();
      if (!closed) setTimeout(connectKline, 3000);
    }};
    ws.onerror = () => {{ klineOk = false; statusLine(); }};
    ws.onmessage = (ev) => {{
      try {{
        const msg = JSON.parse(ev.data);
        const k = msg.k;
        if (!k) return;
        const time = Math.floor(k.t / 1000);
        lastBarTime = time;
        const candle = {{
          time: time,
          open: parseFloat(k.o),
          high: parseFloat(k.h),
          low: parseFloat(k.l),
          close: parseFloat(k.c),
        }};
        if (BOOT.aggTradeWsUrl) {{
          currentBarOpen = candle.open;
          currentBarHigh = candle.high;
          currentBarLow = candle.low;
        }}
        const up = candle.close >= candle.open;
        const vol = {{
          time: time,
          value: parseFloat(k.v),
          color: up ? '#26a69a96' : '#ef535096',
        }};
        candleSeries.update(candle);
        volumeSeries.update(vol);
      }} catch (e) {{}}
    }};
  }}

  function connectMark() {{
    if (!BOOT.markPriceWsUrl || closed) return;
    const ws = new WebSocket(BOOT.markPriceWsUrl);
    ws.onopen = () => {{ markOk = true; statusLine(); }};
    ws.onclose = () => {{
      markOk = false;
      statusLine();
      if (!closed) setTimeout(connectMark, 3000);
    }};
    ws.onerror = () => {{ markOk = false; statusLine(); }};
    ws.onmessage = (ev) => {{
      try {{
        const msg = JSON.parse(ev.data);
        if (msg.e !== 'markPriceUpdate' || msg.p == null) return;
        setMarkDisplay(msg.p);
      }} catch (e) {{}}
    }};
  }}

  function connectAggTrade() {{
    if (!BOOT.aggTradeWsUrl || closed) return;
    const ws = new WebSocket(BOOT.aggTradeWsUrl);
    ws.onopen = () => {{
      aggOk = true;
      startAggRateTimer();
      statusLine();
    }};
    ws.onclose = () => {{
      aggOk = false;
      statusLine();
      aggRateEl.textContent = '';
      if (!closed) setTimeout(connectAggTrade, 3000);
    }};
    ws.onerror = () => {{ aggOk = false; statusLine(); }};
    ws.onmessage = (ev) => {{
      try {{
        const msg = JSON.parse(ev.data);
        if (msg.e !== 'aggTrade' || msg.p == null) return;
        const px = parseFloat(msg.p);
        const mBuyerMaker = !!msg.m;
        queueAggTrade(px, mBuyerMaker);
      }} catch (e) {{}}
    }};
  }}

  if (!BOOT.wsUrl && !BOOT.markPriceWsUrl && !BOOT.aggTradeWsUrl) {{
    st.textContent = '未設定 WebSocket（僅顯示歷史 K）';
    tradePxEl.textContent = '（未連線）';
    return;
  }}

  statusLine();
  if (BOOT.wsUrl) connectKline();
  if (BOOT.markPriceWsUrl) connectMark();
  if (BOOT.aggTradeWsUrl) connectAggTrade();
}})();
</script>
</body></html>"""
