param([switch]$Demo)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
. (Join-Path $root 'start-common.ps1')

$python = Ensure-Backend $root
$node = Ensure-Frontend $root

& $python (Join-Path $root 'backend\tools\prepare_samples.py')
if ($LASTEXITCODE -ne 0) { throw '准备示例图片失败。' }

$logs = New-Item -ItemType Directory -Force -Path (Join-Path $root '.run')
if (!$Demo -and !(Get-NetTCPConnection -State Listen -LocalPort 8100 -ErrorAction SilentlyContinue)) {
    $env:WOMINMIN_DESKTOP_LIBRARY = '1'
    Start-Process -FilePath $python -ArgumentList '-m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8100 --no-access-log' `
        -WorkingDirectory (Join-Path $root 'backend') -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logs.FullName 'backend.out.log') `
        -RedirectStandardError (Join-Path $logs.FullName 'backend.err.log')
}
$env:VITE_PORT = '5199'
$env:VITE_BACKEND_URL = 'http://127.0.0.1:8100'
$env:VITE_DEFAULT_DATA_MODE = if ($Demo) { 'demo' } else { 'real' }
if (!(Get-NetTCPConnection -State Listen -LocalPort 5199 -ErrorAction SilentlyContinue)) {
    Start-Process -FilePath $node -ArgumentList 'node_modules/vite/bin/vite.js' `
        -WorkingDirectory (Join-Path $root 'frontend') -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logs.FullName 'frontend.out.log') `
        -RedirectStandardError (Join-Path $logs.FullName 'frontend.err.log')
}
if ($Demo) { 'http://127.0.0.1:5199/main?data=demo' } else { 'http://127.0.0.1:5199/main?data=live' }
