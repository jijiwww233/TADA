param([string]$Adb = 'adb', [int]$DesktopPort = 5199, [int]$DevicePort = 18101)
$ErrorActionPreference = 'Stop'
$endpoint = "http://127.0.0.1:$DesktopPort/api/phone-import/pairing?transport=usb"
$reply = Invoke-RestMethod $endpoint -Method Post -Headers @{'X-Library-Client'='desktop'}
$pcUrl = [Uri]$reply.url
& $Adb reverse "tcp:$DevicePort" "tcp:$($pcUrl.Port)"
if ($LASTEXITCODE -ne 0) { throw 'Could not create the USB connection.' }
$phoneUrl = "http://127.0.0.1:$DevicePort/$($pcUrl.Fragment)"
$preference = '<?xml version="1.0" encoding="utf-8"?><map><string name="pair">' + [System.Security.SecurityElement]::Escape($phoneUrl) + '</string></map>'
& $Adb shell am force-stop dev.mousehole.collection | Out-Null
$preference | & $Adb shell -T "run-as dev.mousehole.collection sh -c 'umask 077; mkdir -p shared_prefs; cat > shared_prefs/collection.xml'"
if ($LASTEXITCODE -ne 0) { throw 'Could not configure the Android debug build.' }
& $Adb shell am start -n dev.mousehole.collection/.MainActivity | Out-Null
'USB pairing configured for the Android debug build.'
