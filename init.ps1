# 首次初始化：入库 -> 建图 -> 题库 -> 训练（幂等，可重复执行）
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
$python = $env:PYTHON
if (-not $python) { $python = Join-Path $root ".venv\Scripts\python.exe" }
if (-not $python -or -not (Test-Path $python)) { $python = "D:\pythonproject\.venv\Scripts\python.exe" }
if (-not (Test-Path $python)) { throw "未找到 Python：优先设置环境变量 PYTHON，或先执行 .\up.ps1 自动创建 .venv" }
Write-Host "[init] 使用解释器: $python"
& $python -c "from kb.auto_add import sync_once; print(sync_once())"
& $python build_graph.py
& $python -c "from kb.question_bank import generate; print(len(generate()))"
& $python train.py
Write-Host "初始化完成。执行 .\up.ps1 起服务，.\verify.ps1 全量验证。" -ForegroundColor Green
