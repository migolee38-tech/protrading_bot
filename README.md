# 量化交易機器人 · Streamlit 儀表板

幣安 USDT 永續／現貨 · Top 100 榜單 · 即時 K 線 · 多策略訊號 · 模擬下單 · 回測覆盤。

## 本機啟動

```bash
cd trading-bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run streamlit_app.py
```

或：`./start_dashboard.sh`

## 部署到 Zeabur（建議：亞洲機房、永續行情）

請依 **[DEPLOY_ZEABUR.md](./DEPLOY_ZEABUR.md)** 逐步操作（使用專案內 `Dockerfile`，區域建議 **Singapore**）。

## 部署到 Streamlit Community Cloud（公開網址）

請依 **[DEPLOY_STREAMLIT_CLOUD.md](./DEPLOY_STREAMLIT_CLOUD.md)** 逐步操作。

> 美國主機可能無法連幣安永續 API（HTTP 451）；若要以 **永續價格** 為主，請優先使用 Zeabur。

部署成功後會得到類似 `https://你的應用名稱.streamlit.app` 的網址，可分享給他人瀏覽（不需你的電腦 IP）。

## 說明文件

- [README_DASHBOARD.md](./README_DASHBOARD.md) — 功能與操作
- [docs/charting_library_binance_datafeed.md](./docs/charting_library_binance_datafeed.md) — TradingView 資料介面評估

## 授權與風險

僅供學習與模擬；實盤需自行承擔風險。勿將 API 密鑰提交至 Git。
