#!/usr/bin/env bash
# 首次初始化：入库 -> 建图 -> 题库 -> 训练（幂等，可重复执行）
# 容器内由 up.sh 自动调用；宿主机直接执行也可。
set -euo pipefail
cd "$(dirname "$0")"

# Python 解释器：容器内为当前 python；宿主机可用 PYTHON 环境变量指定
PY="${PYTHON:-python3}"
[ -x "$PY" ] || PY=python

echo "[init] 使用解释器: $PY ($($PY -V 2>&1))"
"$PY" -c "from kb.auto_add import sync_once; print(sync_once())"
"$PY" build_graph.py
"$PY" -c "from kb.question_bank import generate; print(len(generate()))"
"$PY" train.py
echo "[init] 初始化完成。执行 ./up.sh 起服务，./verify.sh 全量验证。"
