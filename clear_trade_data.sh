#!/usr/bin/env bash
# 清空本地 bot 訂單與自動交易 state（不影響 Binance 交易所紀錄）
set -euo pipefail
cd "$(dirname "$0")"

if [[ -d .venv ]]; then
  source .venv/bin/activate
fi

python - <<'PY'
from core.trade_data_store import clear_local_trade_data

removed = clear_local_trade_data()
if removed:
    print("已刪除：")
    for p in removed:
        print(f"  - {p}")
else:
    print("沒有找到可清空的本地成交／訂單檔案。")
print("（Binance Testnet／主網上的持倉與成交需至交易所自行平倉／查看）")
PY
