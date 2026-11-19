param([string]$HostAddress)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
. (Join-Path $root 'start-common.ps1')

$python = Ensure-Backend $root

if (!$HostAddress) {
    $connected = @(Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -and $_.IPv4Address })
    $wifi = @($connected | Where-Object { $_.InterfaceAlias -match 'Wi-Fi|WLAN' -or $_.InterfaceDescription -match 'Wireless|802\.11' })
    $choice = if ($wifi.Count -eq 1) { $wifi[0] } elseif ($connected.Count -eq 1) { $connected[0] }
    if (!$choice -or @($choice.IPv4Address).Count -ne 1) {
        throw '请用 -HostAddress 参数传入电脑的 Wi-Fi IPv4 地址。'
    }
    $HostAddress = $choice.IPv4Address.IPAddress
}
Push-Location -LiteralPath (Join-Path $root 'backend')
try {
    $env:WOMINMIN_DESKTOP_LIBRARY = '1'
    & $python -m tools.phone_import --host $HostAddress
}
finally {
    Pop-Location
}
