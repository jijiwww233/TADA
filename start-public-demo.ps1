param([switch]$IncludeLocalLibrary)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
$vite = Join-Path $root 'frontend\node_modules\vite\bin\vite.js'
if (!(Test-Path -LiteralPath $python) -or !(Test-Path -LiteralPath $vite)) {
    throw 'Install the dependencies as described in README.md.'
}
$env:PYTHONUTF8 = '1'
$env:VITE_DEFAULT_DATA_MODE = 'real'
$env:VITE_PUBLIC_DEMO = '1'
if (Get-NetTCPConnection -State Listen -LocalPort 18080 -ErrorAction SilentlyContinue) {
    throw 'Port 18080 is occupied. Stop the previously launched public demo before restarting.'
}
$logs = New-Item -ItemType Directory -Force -Path (Join-Path $root '.run')
$expected = Join-Path $logs.FullName 'public-site'
if (($logs.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
    ((Test-Path -LiteralPath $expected) -and ((Get-Item -LiteralPath $expected -Force).Attributes -band [IO.FileAttributes]::ReparsePoint))) {
    throw 'The public build directory cannot be a link.'
}
Push-Location (Join-Path $root 'frontend')
try {
    & node.exe $vite build --config vite.public.config.ts
    if ($LASTEXITCODE) { throw 'Public frontend build failed.' }
}
finally { Pop-Location }
$backend = Join-Path $root 'backend'
Push-Location $backend
try {
    $seedArgs = @('-m', 'tools.seed_public_demo')
    if ($IncludeLocalLibrary) { $seedArgs += '--include-local-library' }
    & $python @seedArgs
    if ($LASTEXITCODE) { throw 'Public image import failed.' }
}
finally { Pop-Location }
$process = Start-Process -FilePath $python -ArgumentList '-m uvicorn tools.public_demo:create_public_app --factory --host 127.0.0.1 --port 18080 --no-access-log' `
    -WorkingDirectory $backend -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $logs.FullName 'public-demo.out.log') `
    -RedirectStandardError (Join-Path $logs.FullName 'public-demo.err.log')
for ($attempt = 0; $attempt -lt 50; $attempt++) {
    if ($process.HasExited) { throw 'Public backend exited. See .run/public-demo.err.log.' }
    try {
        if ((Invoke-RestMethod 'http://127.0.0.1:18080/api/health' -TimeoutSec 1).ok) {
            'http://127.0.0.1:18080'
            return
        }
    }
    catch {}
    Start-Sleep -Milliseconds 250
}
throw 'Public backend did not become ready. See .run/public-demo.err.log.'
