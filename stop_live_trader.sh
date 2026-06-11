#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
PID_FILE="logs/live_trader.pid"

if [[ ! -f "$PID_FILE" ]]; then
  echo "找不到 $PID_FILE，背景程序可能未啟動。"
  exit 1
fi

PID=$(cat "$PID_FILE")
if kill -0 "$PID" 2>/dev/null; then
  kill "$PID"
  echo "已停止多策略自動交易 (PID $PID)"
else
  echo "程序 $PID 已不存在。"
fi
rm -f "$PID_FILE"
