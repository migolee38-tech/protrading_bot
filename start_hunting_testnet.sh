#!/usr/bin/env bash
# 已改為多策略入口，此腳本轉發至 start_live_trader.sh
echo "提示：已改為多策略自動交易，請使用 ./start_live_trader.sh"
exec "$(dirname "$0")/start_live_trader.sh" "$@"
