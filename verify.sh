#!/usr/bin/env bash
# 全量验证：输出 PASS/FAIL 并落盘 logs/verify-<时间戳>.log
# 等价 Windows 入口：.\verify.ps1
set -uo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  if [ -d .venv ]; then PY=".venv/bin/python"; else PY="$(command -v python3 || command -v python)"; fi
fi

mkdir -p logs
stamp="$(date +%Y%m%d-%H%M)"
"$PY" verify.py 2>&1 | tee "logs/verify-${stamp}.log"
exit "${PIPESTATUS[0]}"
