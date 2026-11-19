param([string]$HostAddress)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
if (!$HostAddress) {
    $connected = @(Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -and $_.IPv4Address })
    $wifi = @($connected | Where-Object { $_.InterfaceAlias -match 'Wi-Fi|WLAN' -or $_.InterfaceDescription -match 'Wireless|802\.11' })
    $choice = if ($wifi.Count -eq 1) { $wifi[0] } elseif ($connected.Count -eq 1) { $connected[0] }
    if (!$choice -or @($choice.IPv4Address).Count -ne 1) {
        throw 'Pass -HostAddress with the PC Wi-Fi IPv4 address.'
    }
    $HostAddress = $choice.IPv4Address.IPAddress
}
Push-Location -LiteralPath (Join-Path $root 'backend')
try {
    $env:WOMINMIN_DESKTOP_LIBRARY = '1'
    & (Join-Path $root '.venv\Scripts\python.exe') -m tools.phone_import --host $HostAddress
} finally {
    Pop-Location
}
