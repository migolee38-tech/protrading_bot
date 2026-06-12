#!/usr/bin/env bash
# 多策略 24/7 自動交易 — 多帳戶單進程輪詢
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -d .venv ]]; then
  echo "找不到 .venv，請先：python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
  exit 1
fi

source .venv/bin/activate
mkdir -p logs data/orders data/live_trader_state

MODE="testnet"
BG=false
VERIFY=false
for arg in "$@"; do
  case "$arg" in
    --paper) MODE="paper" ;;
    --live) MODE="live" ;;
    --bg) BG=true ;;
    --verify) VERIFY=true ;;
  esac
done

if [[ "$VERIFY" == true ]]; then
  exec python live_runner.py --verify-only --profiles all
fi

PROFILE_SPEC="all"
if [[ "$MODE" == "paper" ]]; then
  PROFILE_SPEC="account1:paper,account2:paper"
elif [[ "$MODE" == "testnet" ]]; then
  PROFILE_SPEC="account1:paper,account1:testnet,account2:paper,account2:testnet"
fi

COMMON_ARGS=(
  --profiles "$PROFILE_SPEC"
  --strategies all
  --top-n 100
  --scan-interval 30
  --total-capital 1000
  --position-pct 1
  --leverage 10
)

if [[ "$MODE" == "live" ]]; then
  COMMON_ARGS=(--profiles all --strategies all --top-n 100 --scan-interval 30
    --total-capital 1000 --position-pct 1 --leverage 10 --confirm-live)
  echo "⚠️  主網實盤：--profiles all 含 live profile 時使用真實資金"
fi

if [[ ! -f .env ]]; then
  echo "需要 .env，請參考 .env.example 設定多帳戶金鑰"
  exit 1
fi

if [[ "$BG" == true ]]; then
  PID_FILE="logs/live_trader.pid"
  if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "已在背景執行 (PID $(cat "$PID_FILE"))。停止: ./stop_live_trader.sh"
    exit 0
  fi
  LOG="logs/live_multi_$(date +%Y%m%d_%H%M%S).log"
  nohup python live_runner.py "${COMMON_ARGS[@]}" >> "$LOG" 2>&1 &
  echo $! > "$PID_FILE"
  echo "✅ 多帳戶多策略 24/7  PID=$(cat "$PID_FILE")  日誌: tail -f $LOG"
  exit 0
fi

echo "啟動多帳戶自動交易（--profiles all，前景）"
exec python live_runner.py "${COMMON_ARGS[@]}"
