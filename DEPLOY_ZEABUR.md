# 部署到 Zeabur（建議：亞洲機房 + 永續行情）

Zeabur 伺服器可選 **新加坡** 等亞洲區域，較容易直連 `fapi.binance.com`（永續 REST），避免 Streamlit Cloud（美國）常見的 **HTTP 451**。

本專案已包含 **`Dockerfile`**，Zeabur 會自動用 Docker 建置。

---

## 前置條件

1. [GitHub](https://github.com) 上已有本專案（例如 `migolee38-tech/protrading_bot`）
2. [Zeabur](https://zeabur.com) 帳號（可用 GitHub 登入）
3. 本機已 `git push` 最新程式（含 `Dockerfile`、`core/market_data.py` 等）

---

## 步驟 1：推送到 GitHub

```bash
cd /Users/Migo_1_2/Documents/trading-bot

git add Dockerfile .dockerignore DEPLOY_ZEABUR.md
git add core/market_data.py core/universe.py streamlit_app.py
# 其餘有修改的檔案一併加入

git commit -m "Add Zeabur Dockerfile and deployment docs"
git push
```

確認 GitHub 根目錄可見：`streamlit_app.py`、`Dockerfile`、`requirements.txt`。

---

## 步驟 2：在 Zeabur 建立專案

1. 登入 https://zeabur.com  
2. **Create Project**  
3. **Region**：選 **Singapore（新加坡）** 或最接近亞洲的區域  
4. 專案內 **Add Service** → **Git**  
5. 選擇 repository：`migolee38-tech/protrading_bot`  
6. **Root Directory**：留空（repo 根即應用根）  
7. 建置方式：偵測到 **`Dockerfile`** 後使用 Docker 建置（無需另填啟動指令）

---

## 步驟 3：環境變數（Zeabur → Variables）

### 登入密碼（建議必設，無需升級 Zeabur 方案）

| 變數 | 說明 |
|------|------|
| `APP_LOGIN_PASSWORD` | **登入密碼**（設了即啟用密碼牆；勿提交 Git） |
| `APP_LOGIN_USER` | 登入帳號（選用，預設 `admin`） |

未設定 `APP_LOGIN_PASSWORD` 時，網站**不會**要求登入（僅適合本機測試）。

### 幣安 API（選用）

僅 **實盤下單** 需要；公開行情、回測、模擬下單不需 API。

| 變數 | 說明 |
|------|------|
| `BINANCE_API_KEY` | 幣安 API Key |
| `BINANCE_API_SECRET` | 幣安 API Secret |
| `BINANCE_STRICT_FUTURES` | 設為 `1` 時，永續模式**僅**用 `fapi.binance.com`（REST），且關閉現貨 WS 備援 |
| `BINANCE_ALLOW_SPOT_WS_FALLBACK` | 設為 `1` 才允許永續 WS 失敗時改連 `stream.binance.com`（預設**關閉**） |

本機可把上述變數寫入 `.env`（已在 `.gitignore`）；Zeabur 請只用 **Variables**。

---

## 步驟 4：網域與測試

1. 部署完成後，Zeabur 會提供 `https://xxxx.zeabur.app`  
2. 開啟網址，確認：  
   - 側欄 **Top 100** 能載入  
   - 頂部 **市場 = 永續** 時 K 線、回測正常  
   - 不應再出現 `HTTPError: 451`（若仍 451，可換 Zeabur 區域或查看 Logs）
   - 側欄應顯示 **榜單行情：永續 (fapi)**；K 線區無「非永續 fapi」警告  
3. 可綁定自訂網域（Zeabur 服務設定）

---

## 本機用 Docker 試跑（選用）

```bash
cd trading-bot
docker build -t protrading-bot .
docker run --rm -p 8501:8501 -e PORT=8501 protrading-bot
```

瀏覽器開 http://localhost:8501

---

## 與 Streamlit Cloud 差異

| 項目 | Streamlit Cloud | Zeabur（新加坡） |
|------|-----------------|------------------|
| 幣安永續 REST | 美國 IP 易 451 | 亞洲 IP 通常可連 |
| 中文字體 | 需 `packages.txt` | Dockerfile 已裝 `fonts-noto-cjk` |
| 設定 | share.streamlit.io | Dockerfile + 區域選擇 |

建議 Zeabur 上線穩定後，可停用舊的 `*.streamlit.app`，避免他人連到美國舊環境。

---

## 常見問題

| 問題 | 處理 |
|------|------|
| Build 失敗 | 看 Zeabur Build Log；確認 `requirements.txt` 在 repo 根目錄 |
| 502 / 無法開啟 | 確認 Dockerfile `CMD` 使用 `0.0.0.0` 與 `$PORT` |
| 中文方塊 | 確認映像含 `fonts-noto-cjk`（本 Dockerfile 已含） |
| 仍 451 | 確認區域為新加坡；Logs 是否仍打 `fapi.binance.com` 失敗 |
| 更新程式 | `git push` 後 Zeabur 通常自動重新部署 |
| 想加密碼保護 | Variables 設 `APP_LOGIN_PASSWORD`（不需升級 Zeabur） |
| 已設密碼仍無登入 | Key 須為 `APP_LOGIN_PASSWORD`（勿用空格或 `app login password`）；Save 後 **Redeploy** |
| 最新 Deployment 非 Running | 點開該次部署看 **Logs** 修錯；修復前可能仍連到舊版且無新變數 |
| 側欄黃色／紅色登入提示 | 容器內未讀到變數 → 確認變數在**本 Streamlit 服務**而非僅 Project |
| 價格像現貨不像永續 | F12→WS 若為 `stream.binance.com` 表示走了現貨備援；設 `BINANCE_STRICT_FUTURES=1` 且**不要**開 `BINANCE_ALLOW_SPOT_WS_FALLBACK` |
| 登入後仍被踢出 | 多開分頁正常；清除 Cookie 需重新登入 |

---

## 相關檔案

| 檔案 | 用途 |
|------|------|
| `Dockerfile` | Zeabur 建置與啟動 Streamlit |
| `.dockerignore` | 排除 `.venv`、`.env`、`data/cache` 等 |
| `core/app_auth.py` | 登入閘道（`APP_LOGIN_*` 環境變數） |
| `streamlit_app.py` | 應用進入點 |
| `requirements.txt` | Python 依賴 |
