# 部署到 Streamlit Community Cloud

部署完成後會得到 **公開 HTTPS 網址**（例如 `https://quant-trading-bot.streamlit.app`），任何人可用瀏覽器開啟，不必連你的家用 IP。

> Community Cloud **不提供固定 IP**；若你一定要「某個 IP」白名單，需改用 VPS 自建（本文件以官方免費雲端為主）。

---

## 前置條件

1. [GitHub](https://github.com) 帳號  
2. [Streamlit Community Cloud](https://share.streamlit.io/) 帳號（可用 GitHub 登入）  
3. 本專案資料夾 `trading-bot`（內含 `streamlit_app.py`、`requirements.txt`）

---

## 步驟 1：上傳到 GitHub

在終端機（專案目錄）：

```bash
cd /Users/Migo_1_2/Documents/trading-bot

# 若尚未初始化 git
git init
git add .
git commit -m "Initial commit: Streamlit trading dashboard"

# 在 GitHub 網站建立新 repository（建議名稱：quant-trading-bot）
# 不要勾選 README（本地已有）

git branch -M main
git remote add origin https://github.com/你的帳號/quant-trading-bot.git
git push -u origin main
```

**務必確認** `.env` 沒有被 commit（已在 `.gitignore`）。

檢查：

```bash
git status
# 不應出現 .env
```

---

## 步驟 2：在 Streamlit Cloud 建立 App

1. 開啟 <https://share.streamlit.io/>  
2. 點 **Create app** → **For myself**  
3. **Repository**：選 `你的帳號/quant-trading-bot`  
4. **Branch**：`main`  
5. **Main file path**：`streamlit_app.py`  
   - 若整個 repo 就是 `trading-bot` 內容，填 `streamlit_app.py`  
   - 若 repo 是 `Documents` 且程式在子資料夾，填 `trading-bot/streamlit_app.py`  
6. **App URL**（自訂子網域）：例如 `quant-trading-bot` → 網址為 `https://quant-trading-bot.streamlit.app`  
7. **Advanced settings** → **Python version**：`3.11`（建議）  
8. 點 **Deploy**

首次建置約 2–5 分鐘。成功後右上角會顯示 **Running** 與公開連結。

---

## 步驟 3：Secrets（選用）

公開行情、回測、**模擬下單** 不需 API 密鑰。

僅當要使用 **實盤下單** 時，在 App 頁面：

**Settings** → **Secrets**，貼上（參考 `.streamlit/secrets.toml.example`）：

```toml
BINANCE_API_KEY = "你的金鑰"
BINANCE_API_SECRET = "你的密鑰"
```

儲存後 App 會自動重新啟動。

---

## 步驟 4：分享給他人

把 `https://xxxx.streamlit.app` 連結傳給對方即可。

注意：

- App 為 **公開**（免費方案）；勿在程式或 Secrets 放機密。  
- 觀看者瀏覽器會直連 **幣安 WebSocket** 顯示即時 K 線（與本機相同）。  
- `data/cache/` 在雲端會重新向幣安抓取，第一次載入 Top 100 可能較慢。

---

## 常見問題

| 問題 | 處理 |
|------|------|
| Build 失敗 `No module named ...` | 確認 `requirements.txt` 在與 `streamlit_app.py` 同層目錄 |
| 畫面空白 / 錯誤 | 在 Cloud 點 **Manage app** → **Logs** 查看 traceback |
| 更新程式後網址沒變 | `git push` 後 Cloud 通常會自動 redeploy；否則 **Reboot app** |
| 想限制誰能看 | 免費版無密碼；需 Streamlit 付費方案或改自建 + 反向代理驗證 |
| 中文變亂碼／方塊 | 確認 `packages.txt` 含 `fonts-noto-cjk` 並已 push；Cloud **Reboot app**；瀏覽器用 Chrome、關閉自動翻譯 |
| `HTTPError: 451` 幣安 API | Cloud 主機 IP 被幣安封鎖；程式會自動改抓 `data-api.binance.vision` 現貨行情；永續 REST 不可用時 Top/K 線會用現貨替代 |

---

## 本專案部署相關檔案

| 檔案 | 用途 |
|------|------|
| `streamlit_app.py` | Cloud 進入點 |
| `requirements.txt` | Python 依賴 |
| `.streamlit/config.toml` | 主題與 client 設定 |
| `.gitignore` | 排除 `.env`、`.venv`、`data/cache` 等 |

---

## 更新部署

```bash
git add -A
git commit -m "描述你的修改"
git push
```

Streamlit Cloud 會偵測 push 並重新部署。
