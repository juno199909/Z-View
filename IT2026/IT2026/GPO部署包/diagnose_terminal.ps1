$ErrorActionPreference = 'SilentlyContinue'
$outFile = "$env:PUBLIC\zview-diagnose-result.txt"

function W($text) { $text | Out-File -FilePath $outFile -Append -Encoding UTF8 }

Remove-Item $outFile -Force -ErrorAction SilentlyContinue

W "==== Z-View terminal diagnosis $(Get-Date) ===="
W ""
W "== machine =="
W "hostname : $env:COMPUTERNAME"
W ("ips      : " + ((Get-NetIPAddress -AddressFamily IPv4 | Where-Object {$_.IPAddress -notlike '127.*'} | Select-Object -ExpandProperty IPAddress) -join ', '))
W ""

W "== installed files (C:\Program Files\CMDB-Agent) =="
Get-ChildItem 'C:\Program Files\CMDB-Agent' -Recurse -File | ForEach-Object {
    $h = (Get-FileHash $_.FullName -Algorithm SHA256).Hash
    W ("{0}  {1} bytes  sha256={2}" -f $_.FullName, $_.Length, $h.Substring(0,16))
}
W ""

W "== service =="
Get-Service CMDB-Agent | ForEach-Object { W ("service: " + $_.Name + " = " + $_.Status) }
W ""

W "== processes =="
Get-CimInstance Win32_Process -Filter "Name='Z-View.exe'" | ForEach-Object {
    W ("pid=" + $_.ProcessId + " session=" + $_.SessionId + " cmd=" + $_.CommandLine)
}
W ""

W "== port 9000 =="
$l = netstat -ano | Select-String ':9000'
if ($l) { $l | ForEach-Object { W $_.Line } } else { W "no listener on 9000" }
W ""

W "== backend connectivity =="
$t = Test-NetConnection -ComputerName 172.16.250.120 -Port 8080 -WarningAction SilentlyContinue
W ("tcp 172.16.250.120:8080 reachable = " + $t.TcpTestSucceeded)
try {
    $r = Invoke-WebRequest -Uri 'http://172.16.250.120:8080' -UseBasicParsing -TimeoutSec 8
    W ("http status = " + $r.StatusCode)
} catch {
    W ("http failed: " + $_.Exception.Message)
}
W ""

W "== agent-runtime.log last 40 lines =="
Get-Content 'C:\ProgramData\CMDB-Agent\logs\agent-runtime.log' -Tail 40 | ForEach-Object { W $_ }
W ""

W "== remote-desktop-server.log =="
if (Test-Path 'C:\ProgramData\CMDB-Agent\logs\remote-desktop-server.log') {
    Get-Content 'C:\ProgramData\CMDB-Agent\logs\remote-desktop-server.log' -Tail 20 | ForEach-Object { W $_ }
} else {
    W "(not present)"
}

Write-Host ""
Write-Host "DONE. Result saved to: $outFile"
