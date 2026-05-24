#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

if [[ ! -d .venv ]]; then
  echo "找不到 .venv，請先執行：python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
  exit 1
fi

source .venv/bin/activate
echo "啟動 Streamlit 儀表板 → http://localhost:8501"
echo "停止請在此視窗按 Ctrl+C"
exec streamlit run streamlit_app.py "$@"
