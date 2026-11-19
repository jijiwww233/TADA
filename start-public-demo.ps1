param([switch]$IncludeLocalLibrary)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
. (Join-Path $root 'start-common.ps1')

$python = Ensure-Backend $root
$node = Ensure-Frontend $root
$vite = Join-Path $root 'frontend\node_modules\vite\bin\vite.js'

$env:PYTHONUTF8 = '1'
$env:VITE_DEFAULT_DATA_MODE = 'real'
$env:VITE_PUBLIC_DEMO = '1'
if (Get-NetTCPConnection -State Listen -LocalPort 18080 -ErrorAction SilentlyContinue) {
    throw '端口 18080 已被占用。请先停止之前启动的公网演示版，再重新启动。'
}
$logs = New-Item -ItemType Directory -Force -Path (Join-Path $root '.run')
$expected = Join-Path $logs.FullName 'public-site'
if (($logs.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
    ((Test-Path -LiteralPath $expected) -and ((Get-Item -LiteralPath $expected -Force).Attributes -band [IO.FileAttributes]::ReparsePoint))) {
    throw '公网演示版的构建目录不能是链接。'
}
Push-Location (Join-Path $root 'frontend')
try {
    & $node $vite build --config vite.public.config.ts
    if ($LASTEXITCODE) { throw '公网演示版前端构建失败。' }
}
finally { Pop-Location }
$backend = Join-Path $root 'backend'
Push-Location $backend
try {
    $seedArgs = @('-m', 'tools.seed_public_demo')
    if ($IncludeLocalLibrary) { $seedArgs += '--include-local-library' }
    & $python @seedArgs
    if ($LASTEXITCODE) { throw '演示图片导入失败。' }
}
finally { Pop-Location }
$process = Start-Process -FilePath $python -ArgumentList '-m uvicorn tools.public_demo:create_public_app --factory --host 127.0.0.1 --port 18080 --no-access-log' `
    -WorkingDirectory $backend -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $logs.FullName 'public-demo.out.log') `
    -RedirectStandardError (Join-Path $logs.FullName 'public-demo.err.log')
for ($attempt = 0; $attempt -lt 50; $attempt++) {
    if ($process.HasExited) { throw '演示版后端已退出。请查看 .run/public-demo.err.log。' }
    try {
        if ((Invoke-RestMethod 'http://127.0.0.1:18080/api/health' -TimeoutSec 1).ok) {
            'http://127.0.0.1:18080'
            return
        }
    }
    catch { }
    Start-Sleep -Milliseconds 250
}
throw '演示版后端没有在预期时间内就绪。请查看 .run/public-demo.err.log。'
