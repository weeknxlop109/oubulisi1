# 一键起演示服务：自动完成 初始化 -> 起服务（幂等，无手工补步骤）
# 优先环境变量 PYTHON；默认 D:\pythonproject\.venv；本机 venv 缺失时自动创建。
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

function Resolve-Python {
    if ($env:PYTHON) { return $env:PYTHON }
    $venv = Join-Path $root ".venv\Scripts\python.exe"
    if (Test-Path $venv) { return $venv }
    $default = "D:\pythonproject\.venv\Scripts\python.exe"
    if (Test-Path $default) { return $default }
    # 自动创建本机 .venv 并安装依赖
    Write-Host "[up] 未发现 .venv，正在创建并安装依赖..." -ForegroundColor Yellow
    $py = Get-Command python | Select-Object -ExpandProperty Source
    & $py -m venv .venv
    & $venv -m pip install -q --upgrade pip
    & $venv -m pip install -q -r requirements.txt
    return $venv
}

$python = Resolve-Python
Write-Host "[up] 使用解释器: $python" -ForegroundColor Cyan

# 幂等初始化：首次无图谱/模型时自动 入库+建图+题库+训练
$needInit = -not (Test-Path ".\models\medical_ner_cls\best_model.pt") -or -not (Test-Path ".\data\graph.json")
if ($needInit) {
    Write-Host "[up] 执行初始化（入库 -> 建图 -> 题库 -> 训练）..." -ForegroundColor Cyan
    & $python -c "from kb.auto_add import sync_once; print(sync_once())"
    & $python build_graph.py
    & $python -c "from kb.question_bank import generate; print(len(generate()))"
    & $python train.py
}

$port = if ($env:PORT) { "$env:PORT" } else { "8000" }
Write-Host "启动服务 http://localhost:$port" -ForegroundColor Green
& $python -m uvicorn GUI:app --host 0.0.0.0 --port $port
