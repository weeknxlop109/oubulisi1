# 全量验证：输出 PASS/FAIL 并落盘 logs/verify-<时间戳>.log
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
$python = $env:PYTHON
if (-not $python) { $python = Join-Path $root ".venv\Scripts\python.exe" }
if (-not $python -or -not (Test-Path $python)) { $python = "D:\pythonproject\.venv\Scripts\python.exe" }
if (-not (Test-Path $python)) { throw "未找到 Python：优先设置环境变量 PYTHON" }
$stamp = Get-Date -Format "yyyyMMdd-HHmm"
& $python verify.py *>&1 | Tee-Object -FilePath "logs\verify-$stamp.log"
exit $LASTEXITCODE
