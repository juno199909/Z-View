@echo off
setlocal EnableExtensions EnableDelayedExpansion
title Z-View Deployer

set "INSTALL_DIR=C:\Program Files\CMDB-Agent"
set "DATA_DIR=C:\ProgramData\CMDB-Agent"
set "SERVICE_NAME=CMDB-Agent"
set "SERVICE_DISPLAY_NAME=Z-View Agent"
set "SERVICE_DESCRIPTION=Z-View unified endpoint agent service"
set "BACKEND_TASK_NAME=CMDB Agent Backend"
set "USER_SESSION_TASK_NAME=CMDB Agent User Session"
set "LEGACY_TASK_NAME=CMDB Agent"
set "UI_RUN_VALUE=CMDB-Agent-ConsentUI"
set "EXE_NAME=Z-View.exe"
set "LEGACY_EXE_NAME=CMDB-Agent.exe"
set "LOG_FILE=C:\Windows\Temp\cmdb-agent-deploy.log"
set "COPY_ERROR_LOG=%TEMP%\cmdb-agent-copy-error.log"
set "VD_SYNC_LOG=%TEMP%\cmdb-agent-virtual-display-sync.log"
set "RELAY_LOG=%TEMP%\cmdb-agent-deploy-relay.log"
set "CONTINUITY_STATUS=unknown"
set "CONTINUITY_MESSAGE=remote desktop continuity was not checked"
set "CONTINUITY_CHECK_RC="
set "WAIT_MODE=0"
set "SILENT_MODE=0"
set "EMBEDDED_MODE=0"
set "CURRENT_STAGE=init"

for %%A in (%*) do (
    if /I "%%~A"=="--wait" set "WAIT_MODE=1"
    if /I "%%~A"=="-wait" set "WAIT_MODE=1"
    if /I "%%~A"=="--silent" set "SILENT_MODE=1"
    if /I "%%~A"=="-silent" set "SILENT_MODE=1"
    if /I "%%~A"=="--embedded" set "EMBEDDED_MODE=1"
    if /I "%%~A"=="-embedded" set "EMBEDDED_MODE=1"
)

if not exist "C:\Windows\Temp" mkdir "C:\Windows\Temp" >nul 2>&1
echo [%date% %time%] ==== Z-View deploy started ==== > "%LOG_FILE%"

call :log_info "stage=precheck begin"
call :ensure_admin_context
if errorlevel 1 goto :deploy_failed

if not exist "%~dp0%EXE_NAME%" (
    call :log_error "missing file: %~dp0%EXE_NAME%"
    goto :deploy_failed
)
if not exist "%~dp0config.json" (
    call :log_error "missing file: %~dp0config.json"
    goto :deploy_failed
)

set "CURRENT_STAGE=prepare"
call :ensure_directory "%INSTALL_DIR%"
if errorlevel 1 goto :deploy_failed
call :ensure_directory "%DATA_DIR%"
if errorlevel 1 goto :deploy_failed
call :ensure_directory "%DATA_DIR%\logs"
if errorlevel 1 goto :deploy_failed

set "CURRENT_STAGE=cleanup"
call :stop_existing_runtime
call :cleanup_legacy_artifacts
call :cleanup_stale_runtime_state
call :cleanup_invalid_port_9000_owners

set "CURRENT_STAGE=copy"
call :log_file_fingerprint "source exe before copy" "%~dp0%EXE_NAME%"
call :copy_with_verify "%~dp0%EXE_NAME%" "%INSTALL_DIR%\%EXE_NAME%"
if errorlevel 1 goto :deploy_failed
call :copy_with_verify "%~dp0config.json" "%INSTALL_DIR%\config.json"
if errorlevel 1 goto :deploy_failed
call :log_file_fingerprint "installed exe after copy" "%INSTALL_DIR%\%EXE_NAME%"
call :sync_virtual_display_payloads
call :cleanup_legacy_binaries

set "CURRENT_STAGE=service"
call :create_or_update_service
if errorlevel 1 goto :deploy_failed

set "CURRENT_STAGE=firewall"
call :configure_firewall

set "CURRENT_STAGE=start"
call :start_service_and_verify
if errorlevel 1 goto :deploy_failed

call :log_info "deploy completed successfully; remote_desktop_continuity=%CONTINUITY_STATUS%"
set "EXIT_CODE=0"
if "%SILENT_MODE%"=="0" (
    color 0A
    echo.
    echo Deployment completed successfully.
    if /I "%CONTINUITY_STATUS%"=="ready" (
        echo Remote desktop continuity: READY
    ) else (
        echo Remote desktop continuity: %CONTINUITY_STATUS%
        echo %CONTINUITY_MESSAGE%
    )
    echo Service name: %SERVICE_NAME%
    echo Install dir : %INSTALL_DIR%
    echo Log file    : %LOG_FILE%
)
goto :deploy_exit

:deploy_failed
call :log_error "deploy failed at stage=%CURRENT_STAGE%"
set "EXIT_CODE=1"
if "%SILENT_MODE%"=="0" (
    color 0C
    echo.
    echo Deploy failed.
    echo Log file: %LOG_FILE%
)

:deploy_exit
if not defined EXIT_CODE set "EXIT_CODE=%errorLevel%"
if "%WAIT_MODE%"=="1" (
    echo.
    pause
)
exit /b %EXIT_CODE%

:log_info
set "LOG_MESSAGE=%~1"
echo [%date% %time%] INFO: %LOG_MESSAGE%>> "%LOG_FILE%"
if "%SILENT_MODE%"=="0" echo [%date% %time%] INFO: %LOG_MESSAGE%
exit /b 0

:log_warn
set "LOG_MESSAGE=%~1"
echo [%date% %time%] WARNING: %LOG_MESSAGE%>> "%LOG_FILE%"
if "%SILENT_MODE%"=="0" echo [%date% %time%] WARNING: %LOG_MESSAGE%
exit /b 0

:log_error
set "LOG_MESSAGE=%~1"
echo [%date% %time%] ERROR: %LOG_MESSAGE%>> "%LOG_FILE%"
if "%SILENT_MODE%"=="0" echo [%date% %time%] ERROR: %LOG_MESSAGE%
exit /b 1

:relay_log_file
set "RELAY_LEVEL=%~1"
set "RELAY_SOURCE=%~2"
if not exist "%RELAY_SOURCE%" exit /b 0
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "& {" ^
    "  $level = '%RELAY_LEVEL%';" ^
    "  $source = '%RELAY_SOURCE%';" ^
    "  $deployLog = '%LOG_FILE%';" ^
    "  $silent = '%SILENT_MODE%';" ^
    "  if (-not (Test-Path -LiteralPath $source)) { exit 0 };" ^
    "  foreach ($line in @(Get-Content -LiteralPath $source -Encoding UTF8 -ErrorAction SilentlyContinue)) {" ^
    "    $entry = '[' + (Get-Date).ToString('yyyy/MM/dd ddd HH:mm:ss.ff') + '] ' + $level + ': ' + $line;" ^
    "    Add-Content -LiteralPath $deployLog -Value $entry -Encoding ASCII;" ^
    "    if ($silent -ne '1') { Write-Output $entry }" ^
    "  }" ^
    "}"
del /f /q "%RELAY_SOURCE%" >nul 2>&1
exit /b 0

:log_file_fingerprint
set "FINGERPRINT_LABEL=%~1"
set "FINGERPRINT_PATH=%~2"
if exist "%RELAY_LOG%" del /f /q "%RELAY_LOG%" >nul 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "& {" ^
    "  $label = '%FINGERPRINT_LABEL%';" ^
    "  $path = '%FINGERPRINT_PATH%';" ^
    "  if (-not (Test-Path -LiteralPath $path)) { 'file fingerprint: ' + $label + ' missing path=' + $path; exit 0 };" ^
    "  $item = Get-Item -LiteralPath $path;" ^
    "  $sha256 = [System.Security.Cryptography.SHA256]::Create();" ^
    "  $stream = [System.IO.File]::OpenRead($path);" ^
    "  try { $hash = [BitConverter]::ToString($sha256.ComputeHash($stream)).Replace('-', '') } finally { $stream.Dispose(); $sha256.Dispose() };" ^
    "  'file fingerprint: ' + $label + ' path=' + $item.FullName + ' size=' + $item.Length + ' mtime=' + $item.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss') + ' sha256=' + $hash" ^
    "}" > "%RELAY_LOG%" 2>&1
call :relay_log_file "INFO" "%RELAY_LOG%"
exit /b 0

:ensure_admin_context
net session >nul 2>&1
if %errorLevel% neq 0 (
    call :log_error "administrator privileges required"
    exit /b 1
)
call :log_info "administrator privileges confirmed"
exit /b 0

:ensure_directory
if exist "%~1" exit /b 0
mkdir "%~1" >nul 2>&1
if errorlevel 1 (
    call :log_error "failed to create directory %~1"
    exit /b 1
)
call :log_info "directory ready: %~1"
exit /b 0

:stop_existing_runtime
call :log_info "stopping existing runtime"
sc query "%SERVICE_NAME%" >nul 2>&1
if %errorLevel% equ 0 (
    sc stop "%SERVICE_NAME%" >nul 2>&1
    call :wait_for_service_state "Stopped" 25
    if errorlevel 1 call :log_warn "service stop wait timed out"
    sc delete "%SERVICE_NAME%" >nul 2>&1
    timeout /t 2 /nobreak >nul
    call :log_info "existing service removed"
)

schtasks /query /tn "%BACKEND_TASK_NAME%" >nul 2>&1
if %errorLevel% equ 0 (
    schtasks /end /tn "%BACKEND_TASK_NAME%" >nul 2>&1
    schtasks /delete /tn "%BACKEND_TASK_NAME%" /f >nul 2>&1
    call :log_info "removed legacy backend scheduled task"
)

schtasks /query /tn "%USER_SESSION_TASK_NAME%" >nul 2>&1
if %errorLevel% equ 0 (
    schtasks /end /tn "%USER_SESSION_TASK_NAME%" >nul 2>&1
    schtasks /delete /tn "%USER_SESSION_TASK_NAME%" /f >nul 2>&1
    call :log_info "removed legacy user-session scheduled task"
)

for /f "usebackq delims=" %%T in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object { $_.TaskPath -eq '\' -and $_.TaskName -like 'CMDB Agent User Session*' -and $_.TaskName -ne '%USER_SESSION_TASK_NAME%' } | Select-Object -ExpandProperty TaskName"`) do (
    schtasks /end /tn "%%T" >nul 2>&1
    schtasks /delete /tn "%%T" /f >nul 2>&1
    call :log_info "removed legacy wildcard user-session scheduled task %%T"
)

schtasks /query /tn "%LEGACY_TASK_NAME%" >nul 2>&1
if %errorLevel% equ 0 (
    schtasks /delete /tn "%LEGACY_TASK_NAME%" /f >nul 2>&1
    call :log_info "removed legacy scheduled task"
)

reg delete "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Run" /v "%UI_RUN_VALUE%" /f >nul 2>&1
call :stop_legacy_python_agent
taskkill /F /T /IM "%EXE_NAME%" >nul 2>&1
taskkill /F /T /IM "%LEGACY_EXE_NAME%" >nul 2>&1
call :wait_for_agent_shutdown
if errorlevel 1 (
    call :log_warn "%EXE_NAME% or %LEGACY_EXE_NAME% still detected after stop request"
) else (
    call :log_info "existing Z-View/legacy agent processes stopped"
)
exit /b 0

:cleanup_legacy_artifacts
call :log_info "cleaning legacy startup artifacts"
sc query "CMDBAgent" >nul 2>&1
if %errorLevel% equ 0 (
    sc stop "CMDBAgent" >nul 2>&1
    sc delete "CMDBAgent" >nul 2>&1
    call :log_info "removed legacy CMDBAgent service"
)
exit /b 0

:cleanup_stale_runtime_state
call :log_info "cleaning stale runtime state"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "& {" ^
    "  $runtimeDir = Join-Path '%DATA_DIR%' 'runtime';" ^
    "  if (-not (Test-Path -LiteralPath $runtimeDir)) { exit 0 }" ^
    "  $patterns = @('user-session-agent-session-*.json','user-session-agent-session-*.json.tmp','consent-ui-session-*.json','consent-ui-session-*.json.tmp');" ^
    "  foreach ($pattern in $patterns) {" ^
    "    Get-ChildItem -LiteralPath $runtimeDir -Filter $pattern -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue" ^
    "  };" ^
    "  exit 0" ^
    "}"
call :log_info "stale runtime state cleaned"
exit /b 0

:cleanup_invalid_port_9000_owners
call :log_info "checking port 9000 owner before install"
if exist "%RELAY_LOG%" del /f /q "%RELAY_LOG%" >nul 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "& {" ^
    "  $listeners = @(Get-NetTCPConnection -LocalPort 9000 -State Listen -ErrorAction SilentlyContinue);" ^
    "  if ($listeners.Count -eq 0) { 'port 9000 cleanup: no listener'; exit 0 }" ^
    "  foreach ($listener in $listeners) {" ^
    "    $proc = Get-WmiObject Win32_Process -Filter ('ProcessId=' + $listener.OwningProcess) -ErrorAction SilentlyContinue;" ^
    "    if (-not $proc) { 'port 9000 cleanup: owner process not found pid=' + $listener.OwningProcess; continue }" ^
    "    $cmd = (($proc.CommandLine -replace '\s+', ' ').Trim());" ^
    "    $isAgent = $proc.Name -in @('%EXE_NAME%', '%LEGACY_EXE_NAME%', 'python.exe') -or $cmd -match 'cmdb_agent|Z-View|CMDB-Agent';" ^
    "    $isValidUserAgent = $proc.Name -eq '%EXE_NAME%' -and $cmd -like '*--user-session-agent*' -and $cmd -notlike '*--service-host*' -and $cmd -notlike '*--run-agent*' -and $cmd -notlike '*--consent-ui*';" ^
    "    if ($isValidUserAgent) { 'port 9000 cleanup: valid existing owner pid=' + $proc.ProcessId + ' session=' + $proc.SessionId + ' cmd=' + $cmd; continue }" ^
    "    if ($isAgent) {" ^
    "      Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue;" ^
    "      'port 9000 cleanup: stopped invalid owner pid=' + $proc.ProcessId + ' session=' + $proc.SessionId + ' name=' + $proc.Name + ' cmd=' + $cmd;" ^
    "    } else {" ^
    "      'port 9000 cleanup: non-agent owner remains pid=' + $proc.ProcessId + ' session=' + $proc.SessionId + ' name=' + $proc.Name + ' cmd=' + $cmd" ^
    "    }" ^
    "  }" ^
    "}" > "%RELAY_LOG%" 2>&1
call :relay_log_file "INFO" "%RELAY_LOG%"
exit /b 0

:cleanup_legacy_binaries
if exist "%DATA_DIR%\%LEGACY_EXE_NAME%" (
    del /f /q "%DATA_DIR%\%LEGACY_EXE_NAME%" >nul 2>&1
    if exist "%DATA_DIR%\%LEGACY_EXE_NAME%" (
        call :log_warn "failed to remove legacy binary %DATA_DIR%\%LEGACY_EXE_NAME%"
    ) else (
        call :log_info "removed legacy binary %DATA_DIR%\%LEGACY_EXE_NAME%"
    )
)
if exist "%INSTALL_DIR%\%LEGACY_EXE_NAME%" (
    del /f /q "%INSTALL_DIR%\%LEGACY_EXE_NAME%" >nul 2>&1
    if exist "%INSTALL_DIR%\%LEGACY_EXE_NAME%" (
        call :log_warn "failed to remove legacy binary %INSTALL_DIR%\%LEGACY_EXE_NAME%"
    ) else (
        call :log_info "removed legacy binary %INSTALL_DIR%\%LEGACY_EXE_NAME%"
    )
)
exit /b 0

:create_or_update_service
call :log_info "creating Windows service"
sc create "%SERVICE_NAME%" binPath= "\"%INSTALL_DIR%\%EXE_NAME%\" --service-host" start= auto obj= LocalSystem DisplayName= "%SERVICE_DISPLAY_NAME%" >nul 2>&1
if errorlevel 1 (
    call :log_error "failed to create Windows service"
    exit /b 1
)
sc description "%SERVICE_NAME%" "%SERVICE_DESCRIPTION%" >nul 2>&1
sc failure "%SERVICE_NAME%" reset= 86400 actions= restart/5000/restart/5000/restart/5000 >nul 2>&1
sc failureflag "%SERVICE_NAME%" 1 >nul 2>&1
sc qc "%SERVICE_NAME%" >nul 2>&1
if errorlevel 1 (
    call :log_error "service verification failed after creation"
    exit /b 1
)
call :log_info "Windows service created"
exit /b 0

:configure_firewall
netsh advfirewall firewall delete rule name="CMDB Agent" >nul 2>&1
netsh advfirewall firewall delete rule name="Z-View Agent" >nul 2>&1
netsh advfirewall firewall add rule name="Z-View Agent" dir=in action=allow program="%INSTALL_DIR%\%EXE_NAME%" enable=yes profile=any >nul 2>&1
if errorlevel 1 (
    call :log_warn "firewall rule creation failed"
    exit /b 0
)
call :log_info "firewall rule configured"
exit /b 0

:start_service_and_verify
call :log_info "starting Windows service"
sc start "%SERVICE_NAME%" >nul 2>&1
call :wait_for_service_state "Running" 35
if errorlevel 1 (
    call :log_error "service did not reach Running state"
    exit /b 1
)
call :log_info "service is Running"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0wait_privileged_service_pipe.ps1" -PipeName "CMDB-Agent-Privileged" -TimeoutSeconds 35
if errorlevel 1 (
    call :log_error "privileged service pipe CMDB-Agent-Privileged not ready"
    call :log_service_host_snapshot
    call :log_runtime_log_tail
    exit /b 1
)
call :log_info "privileged service pipe verified"
call :log_service_host_snapshot

call :wait_for_backend_worker_ready
if errorlevel 1 (
    call :log_error "backend worker --run-agent --no-remote-desktop not detected"
    exit /b 1
)
call :log_info "backend worker detected"
call :log_backend_worker_snapshot

 call :wait_for_user_session_agent_if_interactive
 if errorlevel 1 exit /b 1
 call :verify_remote_desktop_port_owner_if_interactive
 if errorlevel 1 exit /b 1
 call :log_remote_desktop_continuity_snapshot
 exit /b 0

:wait_for_service_state
set "TARGET_STATE=%~1"
set "TIMEOUT_SECONDS=%~2"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "& { $target = '%TARGET_STATE%'; $deadline = (Get-Date).AddSeconds(%TIMEOUT_SECONDS%); while ((Get-Date) -lt $deadline) { $svc = Get-WmiObject Win32_Service -Filter \"Name='%SERVICE_NAME%'\" -ErrorAction SilentlyContinue; if ($svc -and $svc.State -eq $target) { exit 0 }; Start-Sleep -Seconds 1 }; exit 1 }"
exit /b %errorLevel%

:wait_for_backend_worker_ready
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "& { $deadline = (Get-Date).AddSeconds(35); while ((Get-Date) -lt $deadline) { foreach ($proc in @(Get-WmiObject Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.Name -in @('%EXE_NAME%', '%LEGACY_EXE_NAME%') })) { if ($proc.CommandLine -like '*--run-agent*' -and $proc.CommandLine -like '*--no-remote-desktop*') { exit 0 } }; Start-Sleep -Seconds 1 }; exit 1 }"
exit /b %errorLevel%

:wait_for_user_session_agent_if_interactive
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "& {" ^
    "  $hasInteractive = $false;" ^
    "  foreach ($session in @(Get-WmiObject Win32_LogonSession -Filter \"LogonType=2 OR LogonType=10\" -ErrorAction SilentlyContinue)) { $hasInteractive = $true; break };" ^
    "  if (-not $hasInteractive) { exit 2 };" ^
    "  $runtimeDir = Join-Path '%DATA_DIR%' 'runtime';" ^
    "  $deadline = (Get-Date).AddSeconds(25);" ^
    "  while ((Get-Date) -lt $deadline) {" ^
    "    $now = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds();" ^
    "    foreach ($file in @(Get-ChildItem -LiteralPath $runtimeDir -Filter 'user-session-agent-session-*.json' -ErrorAction SilentlyContinue)) {" ^
    "      try { $payload = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8 | ConvertFrom-Json } catch { continue };" ^
    "      $agentPid = [int]($payload.pid);" ^
    "      $updatedAt = [double]($payload.updated_at);" ^
    "      if ($agentPid -le 0 -or $updatedAt -le 0) { continue };" ^
    "      if (($now - $updatedAt) -gt 45) { continue };" ^
    "      if (Get-Process -Id $agentPid -ErrorAction SilentlyContinue) { exit 0 };" ^
    "    };" ^
    "    Start-Sleep -Seconds 1" ^
    "  };" ^
    "  exit 1" ^
    "}"
set "USER_SESSION_RESULT=%errorLevel%"
call :log_interactive_session_snapshot
if "%USER_SESSION_RESULT%"=="0" (
    call :log_info "user-session agent heartbeat detected"
    call :log_user_session_agent_snapshot
    exit /b 0
)
if "%USER_SESSION_RESULT%"=="2" (
    call :log_info "no interactive session detected; user-session agent will start after next logon"
    exit /b 0
)
call :log_warn "interactive session exists but user-session agent heartbeat was not detected yet; attempting interactive bootstrap"
call :log_user_session_agent_snapshot
call :bootstrap_user_session_agent_in_current_session
if errorlevel 1 (
    call :log_warn "interactive bootstrap skipped or failed; check %DATA_DIR%\\logs\\agent-runtime.log"
    call :log_runtime_log_tail
    exit /b 0
)
call :wait_for_user_session_agent_after_bootstrap
if errorlevel 1 (
    call :log_warn "user-session agent heartbeat still not detected after bootstrap; check %DATA_DIR%\\logs\\agent-runtime.log"
    call :log_user_session_agent_snapshot
    call :log_runtime_log_tail
    exit /b 0
)
call :log_info "user-session agent heartbeat detected after interactive bootstrap"
call :log_user_session_agent_snapshot
exit /b 0

:bootstrap_user_session_agent_in_current_session
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "& {" ^
    "  $currentSessionId = [System.Diagnostics.Process]::GetCurrentProcess().SessionId;" ^
    "  if ($currentSessionId -le 0) { exit 2 }" ^
    "  $exePath = Join-Path '%INSTALL_DIR%' '%EXE_NAME%';" ^
    "  if (-not (Test-Path -LiteralPath $exePath)) { exit 1 }" ^
    "  Start-Process -FilePath $exePath -ArgumentList '--user-session-agent' -WorkingDirectory '%INSTALL_DIR%' -WindowStyle Hidden;" ^
    "  exit 0" ^
    "}"
set "BOOTSTRAP_RESULT=%errorLevel%"
if "%BOOTSTRAP_RESULT%"=="0" (
    call :log_info "interactive bootstrap launched in current session"
    exit /b 0
)
if "%BOOTSTRAP_RESULT%"=="2" (
    call :log_warn "current deploy session is non-interactive; bootstrap skipped"
    exit /b 1
)
call :log_warn "failed to launch interactive bootstrap in current session"
exit /b 1

:wait_for_user_session_agent_after_bootstrap
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "& {" ^
    "  $runtimeDir = Join-Path '%DATA_DIR%' 'runtime';" ^
    "  $deadline = (Get-Date).AddSeconds(15);" ^
    "  while ((Get-Date) -lt $deadline) {" ^
    "    $now = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds();" ^
    "    foreach ($file in @(Get-ChildItem -LiteralPath $runtimeDir -Filter 'user-session-agent-session-*.json' -ErrorAction SilentlyContinue)) {" ^
    "      try { $payload = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8 | ConvertFrom-Json } catch { continue };" ^
    "      $agentPid = [int]($payload.pid);" ^
    "      $updatedAt = [double]($payload.updated_at);" ^
    "      if ($agentPid -le 0 -or $updatedAt -le 0) { continue };" ^
    "      if (($now - $updatedAt) -gt 45) { continue };" ^
    "      if (Get-Process -Id $agentPid -ErrorAction SilentlyContinue) { exit 0 };" ^
    "    };" ^
    "    Start-Sleep -Seconds 1" ^
    "  };" ^
    "  exit 1" ^
    "}"
exit /b %errorLevel%

:verify_remote_desktop_port_owner_if_interactive
call :log_port_9000_owner_snapshot
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "& {" ^
    "  $explorerSessions = @(Get-WmiObject Win32_Process -Filter \"Name='explorer.exe'\" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty SessionId -Unique);" ^
    "  $interactiveSessions = @($explorerSessions | Where-Object { $_ -gt 0 });" ^
    "  $interactiveSessions = @($interactiveSessions | Sort-Object -Unique);" ^
    "  if ($interactiveSessions.Count -eq 0) { exit 2 }" ^
    "  $listeners = @(Get-NetTCPConnection -LocalPort 9000 -State Listen -ErrorAction SilentlyContinue);" ^
    "  if ($listeners.Count -eq 0) { exit 1 }" ^
    "  foreach ($listener in $listeners) {" ^
    "    $proc = Get-WmiObject Win32_Process -Filter ('ProcessId=' + $listener.OwningProcess) -ErrorAction SilentlyContinue;" ^
    "    if (-not $proc) { continue }" ^
    "    $cmd = (($proc.CommandLine -replace '\s+', ' ').Trim());" ^
    "    $isValidUserAgent = $proc.Name -eq '%EXE_NAME%' -and $cmd -like '*--user-session-agent*' -and $cmd -notlike '*--service-host*' -and $cmd -notlike '*--run-agent*' -and $cmd -notlike '*--consent-ui*';" ^
    "    if ($isValidUserAgent -and ($interactiveSessions -contains [int]$proc.SessionId)) { exit 0 }" ^
    "  };" ^
    "  exit 1" ^
    "}"
set "PORT_OWNER_VERIFY_RESULT=%errorLevel%"
if "%PORT_OWNER_VERIFY_RESULT%"=="0" (
    call :log_info "remote desktop port owner verified"
    exit /b 0
)
if "%PORT_OWNER_VERIFY_RESULT%"=="2" (
    call :log_info "no interactive desktop detected; remote desktop port owner verification skipped"
    exit /b 0
)
call :log_error "remote desktop port 9000 is not owned by interactive Z-View.exe --user-session-agent"
call :log_user_session_agent_snapshot
call :log_runtime_log_tail
exit /b 1

:log_port_9000_owner_snapshot
if exist "%RELAY_LOG%" del /f /q "%RELAY_LOG%" >nul 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "& {" ^
    "  $lines = @();" ^
    "  foreach ($explorer in @(Get-WmiObject Win32_Process -Filter \"Name='explorer.exe'\" -ErrorAction SilentlyContinue | Sort-Object SessionId, ProcessId)) {" ^
    "    $lines += ('port 9000 verification: explorer pid=' + $explorer.ProcessId + ' session=' + $explorer.SessionId)" ^
    "  };" ^
    "  $listeners = @(Get-NetTCPConnection -LocalPort 9000 -State Listen -ErrorAction SilentlyContinue);" ^
    "  if ($listeners.Count -eq 0) { $lines += 'port 9000 verification: no listener' }" ^
    "  foreach ($listener in $listeners) {" ^
    "    $proc = Get-WmiObject Win32_Process -Filter ('ProcessId=' + $listener.OwningProcess) -ErrorAction SilentlyContinue;" ^
    "    if ($proc) { $lines += ('port 9000 verification: owner pid=' + $proc.ProcessId + ' session=' + $proc.SessionId + ' name=' + $proc.Name + ' cmd=' + (($proc.CommandLine -replace '\s+', ' ').Trim())) }" ^
    "    else { $lines += ('port 9000 verification: owner missing pid=' + $listener.OwningProcess) }" ^
    "  };" ^
    "  $lines | Select-Object -Unique" ^
    "}" > "%RELAY_LOG%" 2>&1
call :relay_log_file "INFO" "%RELAY_LOG%"
exit /b 0

:log_backend_worker_snapshot
if exist "%RELAY_LOG%" del /f /q "%RELAY_LOG%" >nul 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "& {" ^
    "  $match = $null;" ^
    "  foreach ($proc in @(Get-WmiObject Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.Name -in @('%EXE_NAME%', '%LEGACY_EXE_NAME%') })) {" ^
    "    if ($proc.CommandLine -like '*--run-agent*' -and $proc.CommandLine -like '*--no-remote-desktop*') { $match = $proc; break }" ^
    "  };" ^
    "  if ($match) { 'backend worker detail: pid=' + $match.ProcessId + ' session=' + $match.SessionId + ' command=' + ($match.CommandLine -replace '\s+', ' ').Trim() }" ^
    "}" > "%RELAY_LOG%" 2>&1
call :relay_log_file "INFO" "%RELAY_LOG%"
exit /b 0

:log_service_host_snapshot
if exist "%RELAY_LOG%" del /f /q "%RELAY_LOG%" >nul 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "& {" ^
    "  $lines = @();" ^
    "  foreach ($proc in @(Get-WmiObject Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.Name -in @('%EXE_NAME%', '%LEGACY_EXE_NAME%') -and $_.CommandLine -like '*--service-host*' } | Sort-Object SessionId, ProcessId)) {" ^
    "    $lines += ('service-host process: pid=' + $proc.ProcessId + ' parent=' + $proc.ParentProcessId + ' session=' + $proc.SessionId + ' command=' + (($proc.CommandLine -replace '\s+', ' ').Trim()))" ^
    "  };" ^
    "  if ($lines.Count -eq 0) { $lines = @('service-host process: none found') };" ^
    "  $lines | Select-Object -Unique" ^
    "}" > "%RELAY_LOG%" 2>&1
call :relay_log_file "INFO" "%RELAY_LOG%"
exit /b 0

:log_interactive_session_snapshot
if exist "%RELAY_LOG%" del /f /q "%RELAY_LOG%" >nul 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "& {" ^
    "  $lines = @();" ^
    "  foreach ($proc in @(Get-WmiObject Win32_Process -Filter \"Name='explorer.exe'\" -ErrorAction SilentlyContinue)) {" ^
    "    try { $owner = $proc.GetOwner() } catch { $owner = $null };" ^
    "    $user = 'unknown';" ^
    "    if ($owner -and $owner.User) { if ($owner.Domain) { $user = $owner.Domain + '\' + $owner.User } else { $user = $owner.User } };" ^
    "    $lines += ('interactive session: session=' + $proc.SessionId + ' pid=' + $proc.ProcessId + ' user=' + $user)" ^
    "  };" ^
    "  if ($lines.Count -eq 0) { $lines = @('interactive session: none detected via explorer.exe') };" ^
    "  $lines | Select-Object -Unique" ^
    "}" > "%RELAY_LOG%" 2>&1
call :relay_log_file "INFO" "%RELAY_LOG%"
exit /b 0

:log_user_session_agent_snapshot
if exist "%RELAY_LOG%" del /f /q "%RELAY_LOG%" >nul 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "& {" ^
    "  $runtimeDir = Join-Path '%DATA_DIR%' 'runtime';" ^
    "  $now = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds();" ^
    "  $lines = @();" ^
    "  foreach ($file in @(Get-ChildItem -LiteralPath $runtimeDir -Filter 'user-session-agent-session-*.json' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending)) {" ^
    "    try { $payload = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8 | ConvertFrom-Json } catch { continue };" ^
    "    $agentPid = [int]($payload.pid);" ^
    "    $updatedAt = [double]($payload.updated_at);" ^
    "    $age = if ($updatedAt -gt 0) { [math]::Round($now - $updatedAt, 1) } else { -1 };" ^
    "    $alive = $false;" ^
    "    if ($agentPid -gt 0 -and (Get-Process -Id $agentPid -ErrorAction SilentlyContinue)) { $alive = $true };" ^
    "    $lines += ('session-agent heartbeat: file=' + $file.Name + ' session=' + $payload.session_id + ' pid=' + $agentPid + ' alive=' + $alive + ' age_seconds=' + $age + ' updated=' + $payload.updated_at_iso)" ^
    "  };" ^
    "  foreach ($proc in @(Get-WmiObject Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.Name -in @('%EXE_NAME%', '%LEGACY_EXE_NAME%') -and $_.CommandLine -like '*--user-session-agent*' })) {" ^
    "    $lines += ('session-agent process: pid=' + $proc.ProcessId + ' session=' + $proc.SessionId + ' command=' + (($proc.CommandLine -replace '\s+', ' ').Trim()))" ^
    "  };" ^
    "  if ($lines.Count -eq 0) { $lines = @('session-agent heartbeat: none found') };" ^
    "  $lines | Select-Object -Unique" ^
    "}" > "%RELAY_LOG%" 2>&1
call :relay_log_file "INFO" "%RELAY_LOG%"
exit /b 0

:log_runtime_log_tail
if exist "%RELAY_LOG%" del /f /q "%RELAY_LOG%" >nul 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "& {" ^
    "  $logPath = Join-Path (Join-Path '%DATA_DIR%' 'logs') 'agent-runtime.log';" ^
    "  if (-not (Test-Path -LiteralPath $logPath)) { 'runtime-log tail: file not found at ' + $logPath; exit 0 };" ^
    "  'runtime-log tail begin';" ^
    "  Get-Content -LiteralPath $logPath -Tail 12 -Encoding UTF8 | ForEach-Object { 'runtime-log: ' + $_ };" ^
    "  'runtime-log tail end'" ^
    "}" > "%RELAY_LOG%" 2>&1
call :relay_log_file "INFO" "%RELAY_LOG%"
exit /b 0

:log_remote_desktop_continuity_snapshot
if not exist "%~dp0check_remote_desktop_continuity.ps1" (
    call :log_warn "remote desktop continuity checker missing"
    exit /b 0
)
set "CONTINUITY_CHECK_LOG=%TEMP%\z-view-continuity-check.log"
if exist "%CONTINUITY_CHECK_LOG%" del /f /q "%CONTINUITY_CHECK_LOG%" >nul 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0check_remote_desktop_continuity.ps1" > "%CONTINUITY_CHECK_LOG%" 2>&1
set "CONTINUITY_CHECK_RC=%errorLevel%"
if exist "%CONTINUITY_CHECK_LOG%" (
    call :relay_log_file "INFO" "%CONTINUITY_CHECK_LOG%"
)
if "%CONTINUITY_CHECK_RC%"=="0" (
    set "CONTINUITY_STATUS=READY"
    set "CONTINUITY_MESSAGE=remote desktop commercial continuity readiness verified"
    call :log_info "remote desktop commercial continuity readiness verified"
    exit /b 0
)
if "%CONTINUITY_CHECK_RC%"=="2" (
    set "CONTINUITY_STATUS=BLOCKED"
    set "CONTINUITY_MESSAGE=missing persistent display substrate; install a physical/dummy display or a real signed virtual/IDD display driver payload"
    call :log_warn "remote desktop commercial continuity is blocked by missing persistent display substrate"
    call :log_warn "install a physical/dummy display or a real Windows-supported virtual/IDD display driver payload"
    exit /b 0
)
set "CONTINUITY_STATUS=NOT VERIFIED"
set "CONTINUITY_MESSAGE=continuity checker returned rc=%CONTINUITY_CHECK_RC%; run diagnostic.ps1 for details"
call :log_warn "remote desktop commercial continuity readiness is not verified rc=%CONTINUITY_CHECK_RC%"
exit /b 0

:wait_for_agent_shutdown
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "& { $deadline = (Get-Date).AddSeconds(20); while ((Get-Date) -lt $deadline) { $procs = @(Get-WmiObject Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.Name -in @('%EXE_NAME%', '%LEGACY_EXE_NAME%') }); if ($procs.Count -eq 0) { exit 0 }; Start-Sleep -Milliseconds 500 }; exit 1 }"
exit /b %errorLevel%

:stop_legacy_python_agent
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "& { foreach ($proc in @(Get-WmiObject Win32_Process -Filter \"Name='python.exe'\" -ErrorAction SilentlyContinue)) { if ($proc.CommandLine -match 'cmdb_agent_unified_v2\.py') { Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue } }; exit 0 }"
call :log_info "stopped legacy source-mode python agent if present"
exit /b 0

:copy_with_verify
set "SRC=%~1"
set "DST=%~2"
if exist "%COPY_ERROR_LOG%" del /f /q "%COPY_ERROR_LOG%" >nul 2>&1
for /l %%I in (1,1,5) do (
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
        "& {" ^
        "  $src = '%SRC%';" ^
        "  $dst = '%DST%';" ^
        "  $errorLog = '%COPY_ERROR_LOG%';" ^
        "  $stage = $dst + '.stage';" ^
        "  $backup = $dst + '.old';" ^
        "  function Get-Sha256([string]$path) {" ^
        "    $sha256 = [System.Security.Cryptography.SHA256]::Create();" ^
        "    $stream = [System.IO.File]::OpenRead($path);" ^
        "    try { return [BitConverter]::ToString($sha256.ComputeHash($stream)).Replace('-', '') } finally { $stream.Dispose(); $sha256.Dispose() }" ^
        "  }" ^
        "  try {" ^
        "    if (-not (Test-Path -LiteralPath $src)) { throw 'source file missing'; }" ^
        "    $dstDir = Split-Path -Parent $dst;" ^
        "    if (-not (Test-Path -LiteralPath $dstDir)) { New-Item -ItemType Directory -Path $dstDir -Force | Out-Null }" ^
        "    if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Force -ErrorAction SilentlyContinue }" ^
        "    Copy-Item -LiteralPath $src -Destination $stage -Force;" ^
        "    if (-not (Test-Path -LiteralPath $stage)) { throw 'staged copy missing after copy'; }" ^
        "    if ((Get-Item -LiteralPath $src).Length -ne (Get-Item -LiteralPath $stage).Length) { throw 'staged copy size mismatch'; }" ^
        "    if (Test-Path -LiteralPath $backup) { Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue }" ^
        "    if (Test-Path -LiteralPath $dst) {" ^
        "      try {" ^
        "        Move-Item -LiteralPath $dst -Destination $backup -Force;" ^
        "      } catch {" ^
        "        try { Remove-Item -LiteralPath $dst -Force; } catch { throw $_ }" ^
        "      }" ^
        "    }" ^
        "    Move-Item -LiteralPath $stage -Destination $dst -Force;" ^
        "    if (-not (Test-Path -LiteralPath $dst)) { throw 'destination file missing after replace'; }" ^
        "    if ((Get-Item -LiteralPath $src).Length -ne (Get-Item -LiteralPath $dst).Length) { throw 'destination size mismatch after replace'; }" ^
        "    if ((Get-Sha256 $src) -ne (Get-Sha256 $dst)) { throw 'destination hash mismatch after replace'; }" ^
        "    if (Test-Path -LiteralPath $backup) { Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue }" ^
        "    exit 0" ^
        "  } catch {" ^
        "    $message = $_.Exception.Message;" ^
        "    if ($_.InvocationInfo -and $_.InvocationInfo.PositionMessage) { $message = $message + ' | ' + $_.InvocationInfo.PositionMessage }" ^
        "    Set-Content -LiteralPath $errorLog -Value $message -Encoding ASCII;" ^
        "    exit 1" ^
        "  } finally {" ^
        "    if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Force -ErrorAction SilentlyContinue }" ^
        "  }" ^
        "}" >nul 2>&1
    set "COPY_RESULT=!errorLevel!"
    if "!COPY_RESULT!"=="0" (
        call :log_info "copied %~nx1 successfully"
        exit /b 0
    )
    call :log_warn "copy attempt %%I failed for %~nx1"
    if exist "%COPY_ERROR_LOG%" (
        call :log_warn "copy detail recorded at %COPY_ERROR_LOG%"
    )
    timeout /t 2 /nobreak >nul
)
call :log_error "copy failed from %SRC% to %DST% after 5 attempts"
exit /b 1

:sync_virtual_display_payloads
if exist "%VD_SYNC_LOG%" del /f /q "%VD_SYNC_LOG%" >nul 2>&1
if not exist "%~dp0sync_virtual_display_payload.ps1" (
    call :log_warn "virtual display payload sync script missing"
    exit /b 0
)
call :log_info "syncing virtual display payload directories"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0sync_virtual_display_payload.ps1" -SourceRoot "%~dp0Drivers\VirtualDisplay" -InstallRoot "%INSTALL_DIR%" -DataRoot "%DATA_DIR%" > "%VD_SYNC_LOG%" 2>&1
set "VD_SYNC_RC=%errorLevel%"
if exist "%VD_SYNC_LOG%" (
    call :relay_log_file "INFO" "%VD_SYNC_LOG%"
)
if not "%VD_SYNC_RC%"=="0" (
    call :log_warn "virtual display payload sync encountered an error rc=%VD_SYNC_RC%"
) else (
    call :log_info "virtual display payload sync completed"
)
exit /b 0
