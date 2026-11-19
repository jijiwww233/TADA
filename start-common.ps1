# 启动脚本公共部分：自动查找 Python 3.12 和 Node.js 22，第一次运行时安装依赖。
# 用法：在 start-*.ps1 里写  . (Join-Path $PSScriptRoot 'start-common.ps1')

$ErrorActionPreference = 'Stop'

function Write-Note([string]$Message) {
    Write-Host $Message -ForegroundColor Cyan
}

function Show-PythonHelp {
    Write-Host ''
    Write-Host '没有找到可用的 Python 3.12。' -ForegroundColor Yellow
    Write-Host '请按下面步骤安装，装好后重新运行这个脚本：' -ForegroundColor Yellow
    Write-Host '  1. 打开 https://www.python.org/downloads/windows/'
    Write-Host '  2. 找到「Python 3.12.x」，下载「Windows installer (64-bit)」并运行'
    Write-Host '  3. 安装第一屏务必勾选「Add python.exe to PATH」，再点 Install Now'
    Write-Host '  4. 装完后关闭所有终端窗口（或重启电脑）'
}

function Show-NodeHelp {
    Write-Host ''
    Write-Host '没有找到可用的 Node.js（需要 22.12 或更新的 22.x）。' -ForegroundColor Yellow
    Write-Host '请按下面步骤安装，装好后重新运行这个脚本：' -ForegroundColor Yellow
    Write-Host '  1. 打开 https://nodejs.org/en/download'
    Write-Host '  2. 选择「Windows Installer (.msi)」，版本 22.12 或更新的 22.x，64-bit'
    Write-Host '  3. 下载后双击安装，一路点 Next'
    Write-Host '  4. 装完后关闭所有终端窗口（或重启电脑）'
}

function Get-PythonShortVersion([string]$Path) {
    try {
        $output = & $Path -c "import sys; print(str(sys.version_info[0]) + '.' + str(sys.version_info[1]))" 2>$null
        if ($LASTEXITCODE -eq 0 -and $output) { return "$output".Trim() }
    }
    catch { }
    return $null
}

function Find-Python312([string]$Root) {
    $candidates = New-Object System.Collections.Generic.List[string]
    $venv = Join-Path $Root '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $venv) { $candidates.Add($venv) }

    $launcher = Get-Command 'py.exe' -ErrorAction SilentlyContinue
    if ($launcher -and $launcher.Source) {
        try {
            $exe = & $launcher.Source '-3.12' '-c' "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $exe) { $candidates.Add("$exe".Trim()) }
        }
        catch { }
    }

    foreach ($name in @('python3.12.exe', 'python.exe')) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if ($command -and $command.Source) { $candidates.Add($command.Source) }
    }

    $bases = New-Object System.Collections.Generic.List[string]
    if ($env:LOCALAPPDATA) { $bases.Add((Join-Path $env:LOCALAPPDATA 'Programs\Python')) }
    if ($env:ProgramFiles) { $bases.Add($env:ProgramFiles) }
    if (${env:ProgramFiles(x86)}) { $bases.Add(${env:ProgramFiles(x86)}) }
    $bases.Add('C:\')
    foreach ($base in $bases) {
        if (-not (Test-Path -LiteralPath $base)) { continue }
        $candidates.Add((Join-Path $base 'Python312\python.exe'))
        $folders = @(Get-ChildItem -LiteralPath $base -Directory -Filter 'Python312*' -ErrorAction SilentlyContinue)
        foreach ($folder in $folders) {
            $candidates.Add((Join-Path $folder.FullName 'python.exe'))
        }
    }

    $seen = @{}
    $other = $null
    foreach ($candidate in $candidates) {
        if (-not $candidate -or $seen.ContainsKey($candidate)) { continue }
        $seen[$candidate] = $true
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
        $version = Get-PythonShortVersion $candidate
        if ($version -eq '3.12') { return $candidate }
        if ($version -and -not $other) { $other = $version }
    }
    if ($other) {
        Write-Host ("找到的 Python 是 " + $other + "，本项目需要 3.12。") -ForegroundColor Yellow
    }
    return $null
}

function Get-NodeVersionText([string]$Path) {
    try {
        $output = & $Path --version 2>$null
        if ($LASTEXITCODE -eq 0 -and $output) { return "$output".Trim() }
    }
    catch { }
    return $null
}

function Test-NodeVersion([string]$Text) {
    if ($Text -notmatch '^v(\d+)\.(\d+)') { return $false }
    $major = [int]$Matches[1]
    $minor = [int]$Matches[2]
    if ($major -gt 22) { return $true }
    if ($major -eq 22 -and $minor -ge 12) { return $true }
    return $false
}

function Find-Node22([string]$Root) {
    $candidates = New-Object System.Collections.Generic.List[string]
    $command = Get-Command 'node.exe' -ErrorAction SilentlyContinue
    if ($command -and $command.Source) { $candidates.Add($command.Source) }
    if ($env:ProgramFiles) { $candidates.Add((Join-Path $env:ProgramFiles 'nodejs\node.exe')) }
    if (${env:ProgramFiles(x86)}) { $candidates.Add((Join-Path ${env:ProgramFiles(x86)} 'nodejs\node.exe')) }
    if ($env:LOCALAPPDATA) { $candidates.Add((Join-Path $env:LOCALAPPDATA 'Programs\nodejs\node.exe')) }
    if ($env:APPDATA) {
        $nvm = Join-Path $env:APPDATA 'nvm'
        if (Test-Path -LiteralPath $nvm) {
            $folders = @(Get-ChildItem -LiteralPath $nvm -Directory -Filter 'v22*' -ErrorAction SilentlyContinue)
            foreach ($folder in $folders) { $candidates.Add((Join-Path $folder.FullName 'node.exe')) }
        }
    }

    $seen = @{}
    $other = $null
    foreach ($candidate in $candidates) {
        if (-not $candidate -or $seen.ContainsKey($candidate)) { continue }
        $seen[$candidate] = $true
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
        $version = Get-NodeVersionText $candidate
        if ($version -and (Test-NodeVersion $version)) { return $candidate }
        if ($version -and -not $other) { $other = $version }
    }
    if ($other) {
        Write-Host ("找到的 Node.js 是 " + $other + "，需要 22.12 或更新的 22.x。") -ForegroundColor Yellow
    }
    return $null
}

function Get-NpmPath([string]$NodePath) {
    $folder = Split-Path -Parent $NodePath
    $npmCmd = Join-Path $folder 'npm.cmd'
    if (Test-Path -LiteralPath $npmCmd) { return $npmCmd }
    $npmCli = Join-Path $folder 'node_modules\npm\bin\npm-cli.js'
    if (Test-Path -LiteralPath $npmCli) { return $npmCli }
    return $null
}

function Ensure-Backend([string]$Root) {
    $python = Find-Python312 $Root
    if (-not $python) {
        Show-PythonHelp
        throw '找不到 Python 3.12，已停止启动。'
    }
    $venvPython = Join-Path $Root '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $venvPython)) {
        Write-Note '第一次运行：正在创建 Python 虚拟环境（.venv）…'
        & $python -m venv (Join-Path $Root '.venv') | Out-Host
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $venvPython)) {
            throw '创建 .venv 失败。请确认安装 Python 3.12 时勾选了「Add python.exe to PATH」，然后重新运行。'
        }
    }
    $venvVersion = Get-PythonShortVersion $venvPython
    if ($venvVersion -ne '3.12') {
        throw ('.venv 里的 Python 是 ' + $venvVersion + '，本项目需要 3.12。请删除 .venv 文件夹后重新运行这个脚本。')
    }
    $requirements = Join-Path $Root 'backend\requirements.txt'
    $marker = Join-Path $Root '.venv\.dependencies-installed'
    $install = $true
    if ((Test-Path -LiteralPath $marker) -and (Test-Path -LiteralPath $requirements)) {
        $install = (Get-Item -LiteralPath $requirements).LastWriteTime -gt (Get-Item -LiteralPath $marker).LastWriteTime
    }
    if ($install) {
        Write-Note '正在安装后端依赖（第一次需要几分钟，请保持网络畅通）…'
        & $venvPython -m pip install -r $requirements | Out-Host
        if ($LASTEXITCODE -ne 0) {
            throw '后端依赖安装失败。请检查网络或代理后重新运行；也可以按 README 的「手动安装」步骤排查。'
        }
        New-Item -ItemType File -Force -Path $marker | Out-Null
    }
    return $venvPython
}

function Ensure-Frontend([string]$Root) {
    $node = Find-Node22 $Root
    if (-not $node) {
        Show-NodeHelp
        throw '找不到 Node.js 22.12 或更新的 22.x，已停止启动。'
    }
    $frontend = Join-Path $Root 'frontend'
    $vite = Join-Path $frontend 'node_modules\vite\bin\vite.js'
    if (-not (Test-Path -LiteralPath $vite)) {
        $npm = Get-NpmPath $node
        if (-not $npm) {
            throw '找到了 Node.js，但没有找到 npm。请重新安装 Node.js 22（安装包自带 npm），再运行这个脚本。'
        }
        Write-Note '正在安装前端依赖（npm ci，第一次需要几分钟）…'
        if ($npm.EndsWith('.cmd')) {
            & $npm 'ci' '--prefix' $frontend | Out-Host
        }
        else {
            & $node $npm 'ci' '--prefix' $frontend | Out-Host
        }
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $vite)) {
            throw '前端依赖安装失败。请检查网络后重新运行；也可以按 README 的「手动安装」步骤排查。'
        }
    }
    return $node
}
