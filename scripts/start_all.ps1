# Z-View 平台一键启停（#18 批次D：替代分散手工启动）
# 用法：
#   powershell -File scripts\start_all.ps1          # 启动全部服务
#   powershell -File scripts\stop_all.ps1           # 停止全部服务
# 服务清单：主服务(8080/8443) + WT网关(UDP 4433) + 前端preview(4173)
# 软件管理(8081)/软件策略(8082) 已并入主服务（#18 批次A/B），不再单独启动。

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = "C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe"
$Node = "C:\Program Files\nodejs\node.exe"
$LogDir = Join-Path $ProjectRoot "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Test-ZvRunning($ProcName, $Match) {
    $hits = Get-CimInstance Win32_Process -Filter "Name='$ProcName'" |
        Where-Object { $_.CommandLine -match $Match }
    return @($hits)
}

function Start-ZvService($Name, $ProcName, $Exe, $ArgList, $WorkDir, $Match, $OutLog, $ErrLog) {
    $existing = Test-ZvRunning $ProcName $Match
    if ($existing.Count -gt 0) {
        $pids = ($existing | ForEach-Object { $_.ProcessId }) -join ","
        Write-Output "[$Name] already running (PID $pids)"
        return
    }
    Start-Process -FilePath $Exe -ArgumentList $ArgList -WorkingDirectory $WorkDir `
        -WindowStyle Hidden `
        -RedirectStandardOutput $OutLog -RedirectStandardError $ErrLog
    Write-Output "[$Name] started -> $OutLog"
}

function Stop-ZvService($Name, $ProcName, $Match) {
    $hits = Test-ZvRunning $ProcName $Match
    foreach ($p in $hits) {
        Stop-Process -Id $p.ProcessId -Force
        Write-Output "[$Name] stopped (PID $($p.ProcessId))"
    }
}

switch ($MyInvocation.InvocationName) {
    default {
        Write-Output "== Z-View 启动 =="
        Start-ZvService "main(8080/8443)" "python.exe" $Python `
            @("assets_api.py") $ProjectRoot 'assets_api\.py' `
            (Join-Path $LogDir "main_out.log") (Join-Path $LogDir "main_err.log")
        Start-ZvService "wt-gateway(udp4433)" "python.exe" $Python `
            @("webtransport_gateway.py", "--port", "4433") $ProjectRoot 'webtransport_gateway\.py' `
            (Join-Path $LogDir "wt_out.log") (Join-Path $LogDir "wt_err.log")
        Start-ZvService "frontend-preview(4173)" "node.exe" $Node `
            @("node_modules\vite\bin\vite.js", "preview", "--host", "0.0.0.0", "--port", "4173") `
            (Join-Path $ProjectRoot "frontend") 'vite\.js.{0,40}preview' `
            (Join-Path $LogDir "preview_out.log") (Join-Path $LogDir "preview_err.log")
        Write-Output "== 完成（日志在 logs\）=="
    }
}
