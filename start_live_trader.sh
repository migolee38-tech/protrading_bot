#!/usr/bin/env bash
# 多策略 24/7 自動交易 — paper / testnet / live
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -d .venv ]]; then
  echo "找不到 .venv，請先：python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
  exit 1
fi

source .venv/bin/activate
mkdir -p logs data

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
  exec python live_runner.py --verify-only --exec "$MODE"
fi

COMMON_ARGS=(
  --exec "$MODE"
  --strategies all
  --top-n 100
  --scan-interval 30
  --total-capital 1000
  --position-pct 1
  --leverage 10
)

if [[ "$MODE" == "live" ]]; then
  COMMON_ARGS+=(--confirm-live)
  echo "⚠️  主網實盤：使用 BINANCE_API_KEY 真實下單"
fi

if [[ "$MODE" != "paper" && ! -f .env ]]; then
  echo "需要 .env："
  echo "  testnet → BINANCE_TESTNET_API_KEY / BINANCE_TESTNET_API_SECRET"
  echo "  live    → BINANCE_API_KEY / BINANCE_API_SECRET"
  exit 1
fi

if [[ "$BG" == true ]]; then
  PID_FILE="logs/live_trader.pid"
  if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "已在背景執行 (PID $(cat "$PID_FILE"))。停止: ./stop_live_trader.sh"
    exit 0
  fi
  LOG="logs/live_${MODE}_$(date +%Y%m%d_%H%M%S).log"
  nohup python live_runner.py "${COMMON_ARGS[@]}" >> "$LOG" 2>&1 &
  echo $! > "$PID_FILE"
  echo "✅ 多策略 ${MODE} 24/7  PID=$(cat "$PID_FILE")  日誌: tail -f $LOG"
  exit 0
fi

echo "啟動多策略自動交易（${MODE}，前景）"
exec python live_runner.py "${COMMON_ARGS[@]}"
