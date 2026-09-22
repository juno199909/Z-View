# Z-View 平台一键停止（#18 批次D）
# 用法：powershell -File scripts\stop_all.ps1
$ProjectRoot = Split-Path -Parent $PSScriptRoot

function Stop-ZvService($Name, $ProcName, $Match) {
    $hits = Get-CimInstance Win32_Process -Filter "Name='$ProcName'" |
        Where-Object { $_.CommandLine -match $Match }
    foreach ($p in $hits) {
        Stop-Process -Id $p.ProcessId -Force
        Write-Output "[$Name] stopped (PID $($p.ProcessId))"
    }
}

Write-Output "== Z-View 停止 =="
Stop-ZvService "frontend-preview(4173)" "node.exe" 'vite\.js.{0,40}preview'
Stop-ZvService "wt-gateway(udp4433)" "python.exe" 'webtransport_gateway\.py'
Stop-ZvService "main(8080/8443)" "python.exe" 'assets_api\.py'
Write-Output "== 完成 =="
