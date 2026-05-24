# Streamlit 量化交易儀表板

## 功能對照

| 需求 | 實作 |
|------|------|
| 3–4 套策略 | EMA、唐奇安、RSI、MACD |
| 每日 Top100 USDT 成交量 | 幣安永續/現貨 24h，本地日快取 |
| 自動下單 | **模擬盤**預設；實盤需 API + 手動確認 |
| K 線 + 訊號標記 | 單一工作站：頂部多選策略 + **圖表高亮單策略**箭頭；持倉 **「持」** 圓點；右欄模擬／手動下單 |
| K 線即時 | Lightweight Charts + **`@kline_`** + 永續 **`@markPrice@1s`** + **`@aggTrade`** |
| 介面 | **主工作站**（圖表 + 下單）· **回測覆盤** · **模擬成交**（5/20 初版分頁） |
| 側欄（由上而下） | Top 100 表（點列切換 K 線）→ 成交量滑桿 → K 線根數滑桿 |
| 回測覆盤 | 勝率表 + 儲存 JSON 至 `data/reports/` |
| 模擬成交 | `paper_orders.json` 完整紀錄表 |

## 啟動

**一鍵啟動（建議）：**

```bash
cd trading-bot
./start_dashboard.sh
```

首次使用請先賦予執行權限（只需一次）：`chmod +x start_dashboard.sh`

瀏覽器開啟 `http://localhost:8501`；停止請在 Terminal 按 `Ctrl+C`。

**公開網址（給他人瀏覽）**：見 [DEPLOY_STREAMLIT_CLOUD.md](./DEPLOY_STREAMLIT_CLOUD.md) 部署到 Streamlit Community Cloud。

**手動啟動：**

```bash
cd trading-bot
source .venv/bin/activate
pip install -r requirements.txt
streamlit run streamlit_app.py
```

**可選別名**（bash，寫入 `~/.bash_profile` 後 `source ~/.bash_profile`）：

```bash
alias botdash="/Users/Migo_1_2/Documents/trading-bot/start_dashboard.sh"
```

## 你需要手動完成的事

1. **`.env`**：複製 `.env.example` → `.env`，填入幣安 API（僅實盤需要；回測與 K 線不需密鑰）。
2. **實盤前**：先在主工作站「模擬下單」驗證策略；實盤建議先用 [幣安 Testnet](https://testnet.binancefuture.com/)。
3. **永續合約實盤**：目前下單模組為現貨示範；正式永續需改接 `fapi` testnet（可後續迭代）。
5. **圖表 CDN**：① Tab 會自 **https://unpkg.com** 載入 `lightweight-charts`；無法連外網時請用「③ Plotly 舊圖」或展開區備用圖。

## 目錄

- `start_dashboard.sh` — 一鍵啟動儀表板
- `streamlit_app.py` — 主介面
- `core/` — 行情、榜單、回測、圖表、社群、下單
- `core/lightweight_tv.py` — TradingView 風格嵌入圖（Lightweight Charts + WS）
- `strategies/` — 策略邏輯（與 CLI `main.py` 共用）
- `data/cache/` — Top100 日快取
- `data/paper_orders.json` — 模擬單紀錄
- `data/reports/` — 覆盤 JSON 匯出
