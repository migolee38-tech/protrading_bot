# TradingView Charting Library × 幣安即時資料 — 資料介面評估

> 前提：Charting Library **需向 TradingView 申請**並接受其授權條款；本文件**不包含**任何未授權的庫檔，僅評估 **IDatafeedChartApi** 與幣安 REST / WebSocket 的對接方式。  
> 官方文件入口：[Datafeed API](https://www.tradingview.com/charting-library-docs/latest/connecting_data/Datafeed-API) · [IDatafeedChartApi](https://www.tradingview.com/charting-library-docs/latest/api/interfaces/Charting_Library.IDatafeedChartApi)

---

## 1. 總覽：兩條接線路徑

| 路徑 | 說明 | 適用 |
|------|------|------|
| **A. 自製 Datafeed（JS 實作 IDatafeedChartApi）** | Charting Library 在 `datafeed` 選項綁定你的資料物件；由你呼叫幣安或由後端轉發 | 本專案若要 **與現有 Python 少重複**，可讓 **前端 datafeed** 呼叫 **輕量 REST**（Node/FastAPI），或 **前端直連幣安**（僅公開行情） |
| **B. UDF 介面（HTTP）** | 使用 TV 內建 [UDF adapter](https://www.tradingview.com/charting-library-docs/latest/connecting_data/UDF)，並符合 UDF schema 之多個 REST 路由 | 若團隊希望 **資料層統一是 HTTP**，可將幣安 K 線包成 `/history`、`/quotes`（依 UDF 版本）再由 adapter 接上 |

本次評估以 **路徑 A（自製 Datafeed）** 為主——與「幣安即時 feeds」對齊最直接。

---

## 2. 必須實作的 Datafeed API（進階圖）

介面：**`IDatafeedChartApi`**（及外層 `IExternalDatafeed` 的 `onReady`，依文件組合）。

| 方法 | 用途 | 實作要點（幣安） |
|------|------|------------------|
| **`onReady(callback)`** | 回傳 `DatafeedConfiguration`：支援的 resolution、search、marks 等 | `supported_resolutions` 與你實際能從幣安提供的 interval 對齊（見 §4） |
| **`searchSymbols(userInput, exchange, symbolType, onResult)`** | 搜尋商品 | 可呼叫幣安 `exchangeInfo` 快取 **USDT** 交易對，依前綴篩選；或僅支持你儀表板 Top100 |
| **`resolveSymbol(symbolName, onResolve, onError, extension)`** | 解析成 `LibrarySymbolInfo`（時區、sessions、`pricescale`、`minmov`、type） | **`pricescale` / `volume_precision`** 建議來自 **`exchangeInfo` filters**（tickSize），避免手寫 BTC vs 山寨小數位不一致 |
| **`getBars(symbolInfo, resolution, periodParams, onHistory, onError)`** | 歷史 K | 對應幣安 `GET …/klines`；見 §5 |
| **`subscribeBars(symbolInfo, resolution, subscriberUID, onRealtime, listenerGuid)`** | 即時 K | 對應幣安 **`@kline_{interval}`** WebSocket；每則快照呼叫 `onRealtimeCallback(bar)`；見 §6 |
| **`unsubscribeBars(listenerGuid)`** | 取消訂閱 | 關 WS 或多路 multiplex 對應的訂閱 |
| **`getMarks`** / **`getTimescaleMarks`**（可選） | 策略訊號釘點 | 與現有後端 **`scan_signals_for`** 或報表 API 對齊，`supports_marks` 設 true |

**非同步規則（重要）**：所有 callback 需在 **異步情境**呼叫（官方建議可用 `setTimeout(..., 0)`），避免 `Maximum call stack size exceeded`。  
連結：<https://www.tradingview.com/charting-library-docs/latest/connecting_data/Datafeed-API>

---

## 3. TradingView `Bar` 與時間單位

- `Bar`: `{ time, open, high, low, close, volume? }`  
- **`time`**：進階圖文件中的範例多為 **毫秒 Unix**（例如 `time.getTime()`）；幣安 K 線回傳的 `open_time` 亦為 **ms**，對齊成本低。若有型別問題以官方 **Bar** interface 為準。  
- 陣列需 **時間遞增**；`getBars` 對缺失可用 `historyMetadata.noData`。

---

## 4. Resolution（週期）對照 — TV ↔ Binance

Charting Library 常見：`"1"`, `"3"`, `"5"`, `"15"`, `"60"`, `"240"`, `"1D"` 等（字串）。  
幣安 `interval`：`1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 8h, 12h, 1d, 3w, …`（現貨/合約細節以文件為準）。

建議：**建立單一向量表**：

| supported_resolutions (TV) | Binance REST/WS interval |
|---------------------------|---------------------------|
| `1` | `1m` |
| `3` | `3m` |
| `5` | `5m` |
| `15` | `15m` |
| `60` | `1h` |
| `240` | `4h` |
| `1D` | `1d` |

若要周線/月線，需 **由下位週期 resample** 或僅對外宣告 `has_weekly_and_monthly` / 對應幣安支援的較長 interval（依產品需求）。

---

## 5. `getBars` ← 幣安 REST（歷史）

**現貨：** `GET https://api.binance.com/api/v3/klines`  
**U本位永續：** `GET https://fapi.binance.com/fapi/v1/klines`

參數：

- `symbol`：`BTCUSDT`
- `interval`：對照表映射
- `limit`：每請求最多約 **1500**（現貨/合約請查當版文件）
- 分頁：使用 **`startTime` / `endTime`（毫秒）** 與 **`periodParams.from` / `to` / `countBack`**  
  - 官方建議：**盡可能滿足 `countBack` 根**；區間內不足則再往更早拉回。

**對應到 Bar：**

```
open_time -> time (ms)
open, high, low, close (float)
volume -> volume (base asset)
```

可加 `quote_volume` 若你希望 TV volume 為報價幣種（視 `LibrarySymbolInfo` 設定）。

---

## 6. `subscribeBars` ← 幣安 WebSocket（即時）

與現有 **`core/lightweight_tv.py`** / **`core/market_data.py`** 邏輯一致：

**現貨：** `wss://stream.binance.com:9443/ws/<symbol>@kline_<interval>`  
**永續：** `wss://fstream.binance.com/ws/<symbol>@kline_<interval>`

單連線多 stream 也可用 **combined stream**（`/stream?streams=…`）。

每則 `kline` payload 中取：

- **未完成棒**：用 `t`（棒開頭 ms）為 `Bar.time`，`o,h,l,c,v` → 組 `Bar`，呼叫 **`onRealtimeCallback(bar)`**。  
  與文件一致：**同一 `time` 若為「最後一根」會被整根替換**。

**可加強手感（選配，與現有 Lightweight 邏輯對齊）：**

- 另訂 `@aggTrade`：在 **`open` 仍只用 kline** 前提下，對 **當根** refinement `high/low/close`。  
  將結果仍以一個 **`time`** 發給 Charting Library，避免 **time violation**（不可改更早的已定歷史棒）。

---

## 7. `resolveSymbol`（LibrarySymbolInfo）重點

| 欄位 | 幣安建議來源 |
|------|----------------|
| `ticker` / `name` | 與圖上用的一致，例如 `BTCUSDT` |
| `description` | `BTC/USDT` |
| `type` | `crypto` |
| `session` | `24x7`（Crypto 常用） |
| `timezone` | `Etc/UTC` |
| `exchange` | 顯示用：`BINANCE` / `BINANCE_PERP` |
| `minmov`, `pricescale` | **`exchangeInfo` → PRICE_FILTER.tickSize** 換算 |
| `volume_precision` | **LOT_SIZE.stepSize / 或小數規則** |
| `has_intraday`, `intraday_multipliers`, `supported_resolutions` | 與 §4 對齊 |
| `visible_plots_set` | `'ohlcv'` |
| `data_status` | 即時可設 `'streaming'` |

取得方式：啟動時快取 **`/api/v3/exchangeInfo`** 或 **`/fapi/v1/exchangeInfo`**，依 `marketType`（現貨/永續）分支。

---

## 8. 架構選型（給後續實作）

| 選項 | 優點 | 缺點 |
|------|------|------|
| **前端 Datafeed + 瀏覽器直連幣安 WS/HTTPS** | 無後端維護、延遲低 | CORS：**REST 現貨/合約**從瀏覽器打可能被擋 · **WS 一般不經瀏覽器 CORS** · 需在目標環境驗證 |
| **自建 BFF（FastAPI/Node），Datafeed → 呼叫你的 REST** | 統一限速、可加快取、`exchangeInfo` 快取、`Top100` 與現有 **`core.market_data`** 共用 | 多一層維護與部署 |
| **Mixed** | **歷史**走 BFF，`subscribeBars` **瀏覽器直連幣安 WS**（常見） | 需注意 **symbol/market** 一致性 |

---

## 9. 與現有專案的程式對應

| 現有模組 | 可重用／對照 |
|-----------|----------------|
| `core/market_data.fetch_klines` | `getBars` 的 Python 實作參考；Charting Library 側需 TS/JS 或包一層 API |
| `core/lightweight_tv.py` | WS URL、agg 與 mark 的映射可複製到 **subscribeBars 內部** |
| `core/universe.py` | `searchSymbols` / 預設商品清單 |
| 策略 markers | `getMarks` 或 chart **API** 後續再打點 |

---

## 10. 風險與驗證清單

1. **授權**：Charting Library 僅能用於許可環境（勿將未授權 bundle 進公開 repo）。  
2. **`time violation`**：realtime **僅更新最後一根**或 **新路徑更大 `time`**。  
3. **多 `subscribeBars`**：`listenerGuid` 分開管理，換 symbol / resolution 時 **先 unsubscribe**。  
4. **限頻**：`getBars` 連續補史時注意幣安 **rate limit**。  
5. **現貨 vs 永續**：REST/WS Host 不同，**symbols 規則**相同但 **標記價／資金費率**等合約細節不同，勿混用。

---

## 11. 建議後續步驟

1. 取得並在 **私密目錄** 安裝 Charting Library 範例專案，跑官方 **implement_datafeed tutorial**。  
2. 先實作 **`onReady` + `resolveSymbol` + `getBars`（單 symbol、單分辨率）**。  
3. 再接 **`subscribeBars`（純 `@kline_`）** 確認蠟燭即時更新。  
4. （選配）加 **`@aggTrade` refinement**，與本倉 Lightweight 規則一致。  
5. （選配）`getMarks` 串內建策略結果。

---

## 12. 參考連結

- [Datafeed API overview](https://www.tradingview.com/charting-library-docs/latest/connecting_data/Datafeed-API)  
- [IDatafeedChartApi](https://www.tradingview.com/charting-library-docs/latest/api/interfaces/Charting_Library.IDatafeedChartApi)  
- [Streaming implementation tutorial](https://www.tradingview.com/charting-library-docs/latest/tutorials/implement_datafeed_tutorial/Streaming-Implementation)  
- [Datafeed issues (time violation, stack)](https://www.tradingview.com/charting-library-docs/latest/connecting_data/Datafeed-Issues)  
- [Binance Spot klines](https://developers.binance.com/docs/binance-spot-api-docs/rest-api/market-data-endpoints#klinecandlestick-data)  
- [Binance USDT-M futures klines](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Kline-Candlestick-Data)
