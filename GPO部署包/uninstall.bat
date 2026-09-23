@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
title Z-View Client Uninstaller

rem 统一定义安装脚本使用过的服务、任务和目录名称，避免卸载遗漏历史残留。
set "INSTALL_DIR=C:\Program Files\CMDB-Agent"
set "DATA_DIR=C:\ProgramData\CMDB-Agent"
set "SERVICE_NAME=CMDB-Agent"
set "LEGACY_SERVICE_NAME=CMDBAgent"
set "BACKEND_TASK_NAME=CMDB Agent Backend"
set "USER_SESSION_TASK_NAME=CMDB Agent User Session"
set "LEGACY_TASK_NAME=CMDB Agent"
set "UI_RUN_VALUE=CMDB-Agent-ConsentUI"
set "LOG_FILE=C:\Windows\Temp\cmdb-agent-uninstall.log"
set "REMOVE_INSTALL=1"
set "REMOVE_DATA=1"
set "SILENT_MODE=0"
set "WAIT_MODE=1"
set "CLEANUP_FAILED=0"

rem 支持交互式卸载、静默卸载和保留数据目录三种常用场景。
for %%A in (%*) do (
    if /I "%%~A"=="--silent" set "SILENT_MODE=1"
    if /I "%%~A"=="/silent" set "SILENT_MODE=1"
    if /I "%%~A"=="-silent" set "SILENT_MODE=1"
    if /I "%%~A"=="--keep-data" set "REMOVE_DATA=0"
    if /I "%%~A"=="--preserve-data" set "REMOVE_DATA=0"
    if /I "%%~A"=="--keep-install" set "REMOVE_INSTALL=0"
    if /I "%%~A"=="--no-wait" set "WAIT_MODE=0"
    if /I "%%~A"=="--wait" set "WAIT_MODE=1"
)
if "%SILENT_MODE%"=="1" set "WAIT_MODE=0"

if not exist "C:\Windows\Temp" mkdir "C:\Windows\Temp" >nul 2>&1
> "%LOG_FILE%" echo [%date% %time%] ==== Z-View uninstall started ====

call :ensure_admin_context
if errorlevel 1 goto :uninstall_failed

if "%SILENT_MODE%"=="0" (
    echo.
    echo ============================================
    echo    Z-View 客户端卸载程序
    echo ============================================
    echo.
    echo 将停止并删除 CMDB-Agent 服务、计划任务、防火墙规则和注册表启动项。
    if "%REMOVE_INSTALL%"=="1" echo 将删除程序目录：%INSTALL_DIR%
    if "%REMOVE_DATA%"=="1" echo 将删除数据目录：%DATA_DIR%
    if "%REMOVE_DATA%"=="0" echo 将保留数据目录：%DATA_DIR%
    echo.
    choice /C YN /N /M "确认继续卸载？[Y/N]："
    if errorlevel 2 (
        call :log_info "user cancelled uninstall"
        exit /b 2
    )
)

rem 先停止运行态，再删除服务和文件，避免文件被进程占用。
call :stop_runtime
call :remove_scheduled_tasks
call :remove_registry_startup
call :remove_firewall_rules
call :remove_services

if "%REMOVE_INSTALL%"=="1" call :remove_directory "%INSTALL_DIR%" "program directory"
if "%REMOVE_DATA%"=="1" call :remove_directory "%DATA_DIR%" "data directory"

if "%CLEANUP_FAILED%"=="1" goto :uninstall_failed

call :log_info "uninstall completed successfully"
if "%SILENT_MODE%"=="0" (
    echo.
    echo 卸载完成。
    echo 日志文件：%LOG_FILE%
)
if "%WAIT_MODE%"=="1" (
    echo.
    pause
)
exit /b 0

:uninstall_failed
call :log_error "uninstall completed with errors"
if "%SILENT_MODE%"=="0" (
    echo.
    echo 卸载未完全成功，请检查日志：
    echo %LOG_FILE%
)
if "%WAIT_MODE%"=="1" (
    echo.
    pause
)
exit /b 1

:ensure_admin_context
net session >nul 2>&1
if errorlevel 1 (
    call :log_error "administrator privileges are required"
    if "%SILENT_MODE%"=="0" (
        echo 请右键 uninstall.bat，选择“以管理员身份运行”。
    )
    exit /b 1
)
call :log_info "administrator privileges confirmed"
exit /b 0

:stop_runtime
call :log_info "stopping installed and legacy runtime processes"
sc stop "%SERVICE_NAME%" >nul 2>&1
sc stop "%LEGACY_SERVICE_NAME%" >nul 2>&1
taskkill /F /T /IM "Z-View.exe" >nul 2>&1
taskkill /F /T /IM "CMDB-Agent.exe" >nul 2>&1

rem 兼容历史源码模式部署，按命令行特征停止旧 Python Agent，不影响其他 Python 程序。
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "& {" ^
    "  foreach ($proc in @(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" -ErrorAction SilentlyContinue)) {" ^
    "    if ($proc.CommandLine -match 'cmdb_agent_unified_v2\.py') { Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue }" ^
    "  }" ^
    "}"
call :log_info "runtime stop request sent"
exit /b 0

:remove_scheduled_tasks
call :log_info "removing scheduled task remnants"

for %%T in ("%BACKEND_TASK_NAME%" "%USER_SESSION_TASK_NAME%" "%LEGACY_TASK_NAME%") do (
    schtasks /query /tn "%%~T" >nul 2>&1
    if not errorlevel 1 (
        schtasks /end /tn "%%~T" >nul 2>&1
        schtasks /delete /tn "%%~T" /f >nul 2>&1
        if errorlevel 1 (
            call :log_warn "failed to remove scheduled task %%~T"
            set "CLEANUP_FAILED=1"
        ) else (
            call :log_info "removed scheduled task %%~T"
        )
    )
)

rem 删除历史版本可能生成的带后缀用户会话任务，避免把 PowerShell 输出再交给批处理解析。
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "& {" ^
    "  $tasks = @(Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object { $_.TaskPath -eq '\' -and $_.TaskName -like 'CMDB Agent User Session*' });" ^
    "  foreach ($task in $tasks) {" ^
    "    $taskName = $task.TaskPath + $task.TaskName;" ^
    "    schtasks /end /tn $taskName > $null 2>&1;" ^
    "    schtasks /delete /tn $taskName /f > $null 2>&1;" ^
    "  }" ^
    "}"
if errorlevel 1 (
    call :log_warn "failed to remove wildcard user-session scheduled tasks"
    set "CLEANUP_FAILED=1"
) else (
    call :log_info "wildcard user-session scheduled task cleanup completed"
)
exit /b 0

:remove_registry_startup
call :log_info "removing registry startup entry"
reg delete "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Run" /v "%UI_RUN_VALUE%" /f >nul 2>&1
if errorlevel 1 (
    call :log_info "registry startup entry was not present"
) else (
    call :log_info "removed registry startup entry %UI_RUN_VALUE%"
)
exit /b 0

:remove_firewall_rules
call :log_info "removing firewall rules"
netsh advfirewall firewall delete rule name="CMDB Agent" >nul 2>&1
netsh advfirewall firewall delete rule name="Z-View Agent" >nul 2>&1
call :log_info "firewall rule cleanup completed"
exit /b 0

:remove_services
call :remove_service "%SERVICE_NAME%"
call :remove_service "%LEGACY_SERVICE_NAME%"
exit /b 0

:remove_service
set "TARGET_SERVICE=%~1"
sc query "%TARGET_SERVICE%" >nul 2>&1
if errorlevel 1 exit /b 0

call :log_info "removing Windows service %TARGET_SERVICE%"
sc stop "%TARGET_SERVICE%" >nul 2>&1
call :wait_for_service_stop "%TARGET_SERVICE%" 30
sc delete "%TARGET_SERVICE%" >nul 2>&1
if errorlevel 1 (
    call :log_warn "failed to delete Windows service %TARGET_SERVICE%"
    set "CLEANUP_FAILED=1"
) else (
    call :log_info "removed Windows service %TARGET_SERVICE%"
)
exit /b 0

:wait_for_service_stop
set "TARGET_SERVICE=%~1"
set "TIMEOUT_SECONDS=%~2"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "& {" ^
    "  $deadline = (Get-Date).AddSeconds(%TIMEOUT_SECONDS%);" ^
    "  while ((Get-Date) -lt $deadline) {" ^
    "    $svc = Get-CimInstance Win32_Service -Filter \"Name='%TARGET_SERVICE%'\" -ErrorAction SilentlyContinue;" ^
    "    if (-not $svc -or $svc.State -eq 'Stopped') { exit 0 }" ^
    "    Start-Sleep -Seconds 1" ^
    "  }" ^
    "  exit 1" ^
    "}"
if errorlevel 1 call :log_warn "service stop wait timed out for %TARGET_SERVICE%"
exit /b 0

:remove_directory
set "TARGET_DIRECTORY=%~1"
set "DIRECTORY_LABEL=%~2"
if not exist "%TARGET_DIRECTORY%" (
    call :log_info "%DIRECTORY_LABEL% was not present"
    exit /b 0
)

rd /s /q "%TARGET_DIRECTORY%" >nul 2>&1
if exist "%TARGET_DIRECTORY%" (
    call :log_warn "failed to remove %DIRECTORY_LABEL% %TARGET_DIRECTORY%"
    set "CLEANUP_FAILED=1"
) else (
    call :log_info "removed %DIRECTORY_LABEL% %TARGET_DIRECTORY%"
)
exit /b 0

:log_info
>>"%LOG_FILE%" echo [%date% %time%] INFO: %~1
if "%SILENT_MODE%"=="0" echo [INFO] %~1
exit /b 0

:log_warn
>>"%LOG_FILE%" echo [%date% %time%] WARNING: %~1
if "%SILENT_MODE%"=="0" echo [WARNING] %~1
exit /b 0

:log_error
>>"%LOG_FILE%" echo [%date% %time%] ERROR: %~1
if "%SILENT_MODE%"=="0" echo [ERROR] %~1
exit /b 0
