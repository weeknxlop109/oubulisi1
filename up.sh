#!/usr/bin/env bash
# 一键启动（无手工补步骤）：
#   容器内（/app 下且无 venv）自动完成 依赖自检 -> 初始化 -> 起服务；
#   宿主机 macOS/Linux 下：自动创建 .venv、安装依赖、初始化、起服务。
# 等价 Windows 入口：.\up.ps1
set -euo pipefail
cd "$(dirname "$0")"

in_container() { [ -f /.dockerenv ] || grep -qs docker /proc/1/cgroup 2>/dev/null; }

PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  if in_container; then
    PY="$(command -v python3 || command -v python)"
  elif [ -d .venv ]; then
    PY=".venv/bin/python"
  else
    echo "[up] 未发现 .venv，正在创建并安装依赖..."
    python3 -m venv .venv
    PY=".venv/bin/python"
    "$PY" -m pip install -q --upgrade pip
    "$PY" -m pip install -q -r requirements.txt
  fi
fi
echo "[up] 使用解释器: $PY ($("$PY" -V 2>&1))"

# 幂等初始化：首次无图谱/模型时自动 入库+建图+题库+训练
if in_container || [ ! -f models/medical_ner_cls/best_model.pt ] || [ ! -f data/graph.json ]; then
  echo "[up] 执行初始化（幂等）..."
  ./init.sh
fi

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
echo "[up] 启动服务 http://localhost:${PORT}"
exec "$PY" -m uvicorn GUI:app --host "$HOST" --port "$PORT"
