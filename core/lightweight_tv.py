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

from core.market_data import MarketType, allow_spot_ws_fallback


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


def binance_futures_stream_names(symbol: str, interval: str) -> list[str]:
    """永續合併串流名稱（單一 WebSocket 承載 K 線 + aggTrade + markPrice）。"""
    s = symbol.replace("/", "").lower()
    iv = interval.strip().lower()
    return [f"{s}@kline_{iv}", f"{s}@aggTrade", f"{s}@markPrice@1s"]


def binance_futures_combined_stream_url(symbol: str, interval: str) -> str:
    """永續：單一 WS 訂閱多串流（較不易被瀏覽器/防火牆擋下）。"""
    streams = "/".join(binance_futures_stream_names(symbol, interval))
    return f"wss://fstream.binance.com/stream?streams={streams}"


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
    title: str,
    symbol: str,
    chart_interval: str,
    market: MarketType = "futures",
    use_live: bool = True,
    chart_height: int = 600,
    ws_url: str = "",
    mark_price_ws_url: str = "",
    mark_price_note: str | None = None,
    agg_trade_ws_url: str = "",
    show_price_header: bool = True,
) -> str:
    """產生可給 streamlit.components.v1.html 的完整 HTML 文件。

    show_price_header=False 時隱藏圖內「標記價／最新成交」兩行（價格改由外層
    Streamlit 伺服器輪詢元件顯示，避免瀏覽器收不到 WS 幀時圖上顯示空白「—」）。
    """
    sym = symbol.replace("/", "").upper()
    price_hdr_style = "" if show_price_header else "display:none;"

    if use_live and market == "futures":
        # 三條獨立 fstream（與現貨相同模式）；合併流在部分環境較易失敗
        combined = ""
        sub_params = []
        ws_url = binance_kline_ws_url(sym, chart_interval, "futures")
        agg_trade_ws_url = binance_agg_trade_ws_url(sym, "futures")
        mark_price_ws_url = binance_mark_price_ws_url(sym, "futures")
        mark_price_note = None
    elif use_live and market == "spot":
        combined = ""
        sub_params = []
        if not ws_url:
            ws_url = binance_kline_ws_url(sym, chart_interval, "spot")
        if not agg_trade_ws_url:
            agg_trade_ws_url = binance_agg_trade_ws_url(sym, "spot")
        mark_price_ws_url = ""
        mark_price_note = mark_price_note or "spot"
    else:
        combined = ""
        sub_params = []
        ws_url = ""
        mark_price_ws_url = ""
        agg_trade_ws_url = ""
        mark_price_note = "offline"

    ws_spot_fallback = market == "futures" and allow_spot_ws_fallback()
    spot_agg_fallback = (
        binance_agg_trade_ws_url(sym, "spot") if ws_spot_fallback else ""
    )
    spot_kline_fallback = (
        binance_kline_ws_url(sym, chart_interval, "spot") if ws_spot_fallback else ""
    )

    boot = {
        "candles": candles,
        "volumes": volumes,
        "markers": markers,
        "market": market,
        "wsUrl": ws_url,
        "combinedFuturesUrl": combined if use_live and market == "futures" else "",
        "futuresSubscribeParams": sub_params if use_live and market == "futures" else [],
        "allowSpotFallback": ws_spot_fallback,
        "spotAggFallbackUrl": spot_agg_fallback,
        "spotKlineFallbackUrl": spot_kline_fallback,
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
    <div id="markRow" style="{price_hdr_style}">
      <span class="mark-label">標記價 (Mark)</span>
      <span id="markPx" class="flat">—</span>
    </div>
    <div id="tradeRow" style="{price_hdr_style}">
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
  if (BOOT.markPriceWsUrl || BOOT.combinedFuturesUrl) {{
    markSeries = chart.addLineSeries({{
      color: '#f0b90b',
      lineWidth: 2,
      priceLineVisible: true,
      lastValueVisible: true,
      title: 'Mark',
    }});
  }}
  if (BOOT.aggTradeWsUrl || BOOT.combinedFuturesUrl) {{
    lastTradeSeries = chart.addLineSeries({{
      color: '#29b6f6',
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: true,
      title: 'Last',
    }});
  }}
  if (!BOOT.markPriceWsUrl && !BOOT.combinedFuturesUrl) {{
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
    if (BOOT.aggTradeWsUrl || BOOT.combinedFuturesUrl) {{
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
    if (BOOT.wsUrl || BOOT.combinedFuturesUrl || usingSpotFallback) {{
      parts.push('K線 ' + (klineOk ? '✓' : '…'));
    }}
    if (BOOT.markPriceWsUrl || BOOT.combinedFuturesUrl) {{
      parts.push('標記價 ' + (markOk ? '✓' : '…'));
    }}
    if (BOOT.aggTradeWsUrl || BOOT.combinedFuturesUrl || usingSpotFallback) {{
      parts.push('Agg ' + (aggOk ? '✓' : '…'));
    }}
    if (usingSpotFallback) parts.push('現貨備援 stream.binance.com');
    else if (activeFeedHost) parts.push('行情 ' + activeFeedHost);
    if (!BOOT.wsUrl && !BOOT.combinedFuturesUrl && !BOOT.markPriceWsUrl && !BOOT.aggTradeWsUrl) {{
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
    if (aggStatTimer || (!BOOT.aggTradeWsUrl && !BOOT.combinedFuturesUrl)) return;
    aggStatTimer = setInterval(() => {{
      if ((BOOT.aggTradeWsUrl || BOOT.combinedFuturesUrl || usingSpotFallback) && aggOk) {{
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
      (BOOT.aggTradeWsUrl || BOOT.combinedFuturesUrl || usingSpotFallback) &&
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
  let usingSpotFallback = false;
  let activeFeedHost = '';
  let futuresWsAttempt = 0;

  function setActiveFeed(url) {{
    if (!url) return;
    try {{
      activeFeedHost = new URL(url).host;
    }} catch (e) {{
      activeFeedHost = url.indexOf('fstream') >= 0 ? 'fstream.binance.com' : 'stream.binance.com';
    }}
  }}

  function unwrapBinanceMsg(raw) {{
    if (raw && raw.stream && raw.data) return raw.data;
    return raw;
  }}

  function handleKlinePayload(k) {{
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
    if (BOOT.aggTradeWsUrl || BOOT.combinedFuturesUrl || usingSpotFallback) {{
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
    klineOk = true;
    statusLine();
  }}

  function handleFuturesPayload(msg) {{
    if (!msg || !msg.e) return;
    if (msg.e === 'kline' && msg.k) {{
      handleKlinePayload(msg.k);
      return;
    }}
    if (msg.e === 'markPriceUpdate' && msg.p != null) {{
      markOk = true;
      setMarkDisplay(msg.p);
      statusLine();
      return;
    }}
    if (msg.e === 'aggTrade' && msg.p != null) {{
      aggOk = true;
      startAggRateTimer();
      const px = parseFloat(msg.p);
      queueAggTrade(px, !!msg.m);
      statusLine();
    }}
  }}

  function futuresWsFailedMessage() {{
    st.textContent = '永續 fstream 連線失敗 · 僅顯示歷史 K（請確認網路或 Zeabur 區域）';
    st.style.color = '#ef5350';
    tradePxEl.textContent = '（永續 WS 未連線）';
  }}

  function startSpotFallback() {{
    if (closed || usingSpotFallback) return;
    if (!BOOT.allowSpotFallback) {{
      futuresWsFailedMessage();
      return;
    }}
    usingSpotFallback = true;
    st.textContent = '永續 fstream 無法連線 · 暫用現貨 WS（僅參考，非 USDT.P）';
    st.style.color = '#f0b90b';
    if (BOOT.spotKlineFallbackUrl) connectKlineUrl(BOOT.spotKlineFallbackUrl);
    if (BOOT.spotAggFallbackUrl) connectAggUrl(BOOT.spotAggFallbackUrl);
  }}

  function connectFuturesSubscribe() {{
    if (closed || !BOOT.futuresSubscribeParams || !BOOT.futuresSubscribeParams.length) return;
    futuresWsAttempt += 1;
    setActiveFeed('wss://fstream.binance.com/ws');
    const ws = new WebSocket('wss://fstream.binance.com/ws');
    ws.onopen = () => {{
      ws.send(JSON.stringify({{
        method: 'SUBSCRIBE',
        params: BOOT.futuresSubscribeParams,
        id: 1,
      }}));
    }};
    ws.onmessage = (ev) => {{
      try {{
        handleFuturesPayload(unwrapBinanceMsg(JSON.parse(ev.data)));
      }} catch (e) {{}}
    }};
    ws.onerror = () => {{
      if (!klineOk && !aggOk && futuresWsAttempt >= 2) startSpotFallback();
    }};
    ws.onclose = () => {{
      klineOk = false;
      markOk = false;
      aggOk = false;
      statusLine();
      if (!closed && futuresWsAttempt < 2) {{
        setTimeout(connectFuturesSubscribe, 3000);
      }} else if (!closed && !klineOk && !aggOk) {{
        startSpotFallback();
      }}
    }};
    setTimeout(() => {{
      if (!closed && !klineOk && !aggOk && !usingSpotFallback) startSpotFallback();
    }}, 12000);
  }}

  function connectFuturesCombined() {{
    if (closed || !BOOT.combinedFuturesUrl) return;
    futuresWsAttempt += 1;
    setActiveFeed(BOOT.combinedFuturesUrl);
    const ws = new WebSocket(BOOT.combinedFuturesUrl);
    ws.onopen = () => {{ statusLine(); }};
    ws.onmessage = (ev) => {{
      try {{
        handleFuturesPayload(unwrapBinanceMsg(JSON.parse(ev.data)));
      }} catch (e) {{}}
    }};
    ws.onerror = () => {{
      if (futuresWsAttempt < 2) {{
        setTimeout(connectFuturesSubscribe, 500);
      }} else {{
        startSpotFallback();
      }}
    }};
    ws.onclose = () => {{
      klineOk = false;
      markOk = false;
      aggOk = false;
      statusLine();
      if (!closed && futuresWsAttempt < 2) {{
        setTimeout(connectFuturesSubscribe, 2000);
      }} else if (!closed && !usingSpotFallback) {{
        startSpotFallback();
      }}
    }};
    setTimeout(() => {{
      if (!closed && !klineOk && !aggOk && !markOk && !usingSpotFallback) {{
        try {{ ws.close(); }} catch (e) {{}}
        connectFuturesSubscribe();
      }}
    }}, 12000);
  }}

  function connectKlineUrl(url) {{
    if (!url || closed) return;
    setActiveFeed(url);
    const ws = new WebSocket(url);
    ws.onopen = () => {{ klineOk = true; statusLine(); }};
    ws.onclose = () => {{
      klineOk = false;
      statusLine();
      if (!closed && !usingSpotFallback) setTimeout(() => connectKlineUrl(url), 3000);
    }};
    ws.onerror = () => {{ klineOk = false; statusLine(); }};
    ws.onmessage = (ev) => {{
      try {{
        const msg = JSON.parse(ev.data);
        const k = msg.k;
        if (!k) return;
        handleKlinePayload(k);
      }} catch (e) {{}}
    }};
  }}

  function connectAggUrl(url) {{
    if (!url || closed) return;
    setActiveFeed(url);
    const ws = new WebSocket(url);
    ws.onopen = () => {{
      aggOk = true;
      startAggRateTimer();
      statusLine();
    }};
    ws.onclose = () => {{
      aggOk = false;
      statusLine();
      aggRateEl.textContent = '';
      if (!closed) setTimeout(() => connectAggUrl(url), 3000);
    }};
    ws.onerror = () => {{ aggOk = false; statusLine(); }};
    ws.onmessage = (ev) => {{
      try {{
        const msg = unwrapBinanceMsg(JSON.parse(ev.data));
        if (msg.e !== 'aggTrade' || msg.p == null) return;
        queueAggTrade(parseFloat(msg.p), !!msg.m);
      }} catch (e) {{}}
    }};
  }}

  function connectKline() {{
    if (!BOOT.wsUrl || closed) return;
    setActiveFeed(BOOT.wsUrl);
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
        if (!msg.k) return;
        handleKlinePayload(msg.k);
      }} catch (e) {{}}
    }};
  }}

  function connectMark() {{
    if (!BOOT.markPriceWsUrl || closed) return;
    setActiveFeed(BOOT.markPriceWsUrl);
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
    setActiveFeed(BOOT.aggTradeWsUrl);
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
        const msg = unwrapBinanceMsg(JSON.parse(ev.data));
        if (msg.e !== 'aggTrade' || msg.p == null) return;
        const px = parseFloat(msg.p);
        const mBuyerMaker = !!msg.m;
        queueAggTrade(px, mBuyerMaker);
      }} catch (e) {{}}
    }};
  }}

  if (!BOOT.wsUrl && !BOOT.combinedFuturesUrl && !BOOT.markPriceWsUrl && !BOOT.aggTradeWsUrl) {{
    st.textContent = '未設定 WebSocket（僅顯示歷史 K）';
    tradePxEl.textContent = '（未連線）';
    return;
  }}

  statusLine();
  if (BOOT.combinedFuturesUrl) {{
    connectFuturesCombined();
  }} else {{
    if (BOOT.wsUrl) connectKline();
    if (BOOT.markPriceWsUrl) connectMark();
    if (BOOT.aggTradeWsUrl) connectAggTrade();
  }}
}})();
</script>
</body></html>"""


def build_order_panel_live_price_html(
    symbol: str,
    market: MarketType = "futures",
) -> str:
    """右欄下單區：與 K 線圖相同 @aggTrade WebSocket，即時最新成交價。"""
    sym = symbol.replace("/", "").upper()
    mkt_label = "永續" if market == "futures" else "現貨"
    agg_url = binance_agg_trade_ws_url(sym, market)
    ws_spot_fallback = market == "futures" and allow_spot_ws_fallback()
    spot_agg = binance_agg_trade_ws_url(sym, "spot") if ws_spot_fallback else ""

    boot = {
        "symbol": sym,
        "market": market,
        "aggTradeWsUrl": agg_url,
        "allowSpotFallback": ws_spot_fallback,
        "spotAggFallbackUrl": spot_agg,
        "mktLabel": mkt_label,
    }
    boot_json = json.dumps(boot, ensure_ascii=False)

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"/>
<style>
  html, body {{
    margin: 0; padding: 0; background: transparent;
    font-family: "Noto Sans CJK TC", "Noto Sans TC", system-ui, sans-serif;
    color: #fafafa;
  }}
  #wrap {{ padding: 2px 4px 0; }}
  .lbl {{ font-size: 0.82rem; color: #a3a8b8; margin-bottom: 2px; }}
  #px {{
    font-size: 1.55rem; font-weight: 700; font-variant-numeric: tabular-nums;
    letter-spacing: 0.02em; line-height: 1.2;
  }}
  #px.buy {{ color: #26a69a; }}
  #px.sell {{ color: #ef5350; }}
  #px.flat {{ color: #fafafa; }}
  #sub {{ font-size: 0.72rem; color: #787b86; margin-top: 2px; }}
</style>
</head>
<body>
<div id="wrap">
  <div class="lbl">最新價</div>
  <div id="px" class="flat">—</div>
  <div id="sub">aggTrade 連線中…</div>
</div>
<script>
(function() {{
  const BOOT = {boot_json};
  const pxEl = document.getElementById('px');
  const subEl = document.getElementById('sub');

  function formatPx(p) {{
    const n = parseFloat(p);
    if (!isFinite(n)) return '—';
    const a = Math.abs(n);
    const d = a >= 1 ? 4 : a >= 0.01 ? 6 : 8;
    return n.toLocaleString('en-US', {{ minimumFractionDigits: 2, maximumFractionDigits: d }});
  }}

  let closed = false;
  let usingSpot = false;
  let gotTick = false;
  let activeHost = '';

  function feedLabel() {{
    if (usingSpot) return '實際 stream.binance.com（現貨備援）';
    if (activeHost) return '實際 ' + activeHost;
    return BOOT.mktLabel + ' · aggTrade';
  }}

  function onAgg(px, buyerMaker) {{
    gotTick = true;
    pxEl.textContent = formatPx(px);
    pxEl.className = buyerMaker ? 'sell' : 'buy';
    subEl.textContent = feedLabel() + ' · 與圖同步';
    subEl.style.color = usingSpot ? '#f0b90b' : '#26a69a';
  }}

  function connectAgg(url, isFallback) {{
    if (!url || closed) return null;
    try {{ activeHost = new URL(url).host; }} catch (e) {{ activeHost = ''; }}
    const ws = new WebSocket(url);
    ws.onopen = () => {{
      usingSpot = !!isFallback;
      subEl.textContent = feedLabel() + ' 已連線';
      subEl.style.color = usingSpot ? '#f0b90b' : '#787b86';
    }};
    ws.onmessage = (ev) => {{
      try {{
        const raw = JSON.parse(ev.data);
        const msg = raw.stream && raw.data ? raw.data : raw;
        if (msg.e !== 'aggTrade' || msg.p == null) return;
        onAgg(parseFloat(msg.p), !!msg.m);
      }} catch (e) {{}}
    }};
    ws.onerror = () => {{
      subEl.textContent = 'aggTrade 連線失敗';
      subEl.style.color = '#ef5350';
    }};
    ws.onclose = () => {{
      if (!closed) setTimeout(() => connectAgg(url, isFallback), 2500);
    }};
    return ws;
  }}

  function scheduleFuturesAggRetry(attempt) {{
    setTimeout(() => {{
      if (closed || gotTick) return;
      if (BOOT.allowSpotFallback && BOOT.spotAggFallbackUrl && BOOT.market === 'futures') {{
        try {{ if (mainWs) mainWs.close(); }} catch (e) {{}}
        connectAgg(BOOT.spotAggFallbackUrl, true);
        return;
      }}
      if (!BOOT.allowSpotFallback && BOOT.market === 'futures' && attempt < 4) {{
        subEl.textContent = '永續 fstream 連線中（重試 ' + (attempt + 1) + '/4）…';
        subEl.style.color = '#787b86';
        try {{ if (mainWs) mainWs.close(); }} catch (e) {{}}
        mainWs = connectAgg(BOOT.aggTradeWsUrl, false);
        scheduleFuturesAggRetry(attempt + 1);
        return;
      }}
      if (!gotTick) {{
        subEl.textContent = '永續 fstream 未收到成交 · 請看下方 REST 參考價';
        subEl.style.color = '#ef5350';
      }}
    }}, attempt === 0 ? 6000 : 5000);
  }}

  let mainWs = connectAgg(BOOT.aggTradeWsUrl, false);
  scheduleFuturesAggRetry(0);

  window.addEventListener('beforeunload', () => {{ closed = true; }});
}})();
</script>
</body></html>"""
