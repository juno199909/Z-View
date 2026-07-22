@echo off
REM Z-View Diagnostic Script
REM Run this on Juno to diagnose collection issues

echo ============================================
echo Z-View Diagnostic Tool
echo ============================================
echo.

echo [Test 1] Checking PowerShell availability...
powershell -Command "Write-Host 'PowerShell is available'" 2>nul
if %errorLevel% neq 0 (
    echo [FAIL] PowerShell not available
) else (
    echo [OK] PowerShell is available
)

echo.
echo [Test 2] Testing hardware collection commands...
echo.
echo Testing: Win32_BIOS SerialNumber ^(CIM/WMI fallback^)
powershell -NoProfile -ExecutionPolicy Bypass -Command "$value = $null; try { $value = Get-CimInstance Win32_BIOS | Select-Object -ExpandProperty SerialNumber } catch {}; if (-not $value) { try { $value = Get-WmiObject Win32_BIOS | Select-Object -ExpandProperty SerialNumber } catch {} }; if ($value) { Write-Host $value; exit 0 } else { exit 1 }" 2>nul
if %errorLevel% neq 0 (
    echo [FAIL] Cannot get BIOS serial number
) else (
    echo [OK] BIOS serial number retrieved
)

echo.
echo Testing: Win32_ComputerSystem Manufacturer ^(CIM/WMI fallback^)
powershell -NoProfile -ExecutionPolicy Bypass -Command "$value = $null; try { $value = Get-CimInstance Win32_ComputerSystem | Select-Object -ExpandProperty Manufacturer } catch {}; if (-not $value) { try { $value = Get-WmiObject Win32_ComputerSystem | Select-Object -ExpandProperty Manufacturer } catch {} }; if ($value) { Write-Host $value; exit 0 } else { exit 1 }" 2>nul
if %errorLevel% neq 0 (
    echo [FAIL] Cannot get manufacturer
) else (
    echo [OK] Manufacturer retrieved
)

echo.
echo Testing: Win32_ComputerSystem Model ^(CIM/WMI fallback^)
powershell -NoProfile -ExecutionPolicy Bypass -Command "$value = $null; try { $value = Get-CimInstance Win32_ComputerSystem | Select-Object -ExpandProperty Model } catch {}; if (-not $value) { try { $value = Get-WmiObject Win32_ComputerSystem | Select-Object -ExpandProperty Model } catch {} }; if ($value) { Write-Host $value; exit 0 } else { exit 1 }" 2>nul
if %errorLevel% neq 0 (
    echo [FAIL] Cannot get model
) else (
    echo [OK] Model retrieved
)

echo.
echo [Test 3] Testing software registry access...
echo.
echo Testing: Registry read (HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall)
powershell -Command "$key = [Microsoft.Win32.Registry]::LocalMachine.OpenSubKey('SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall'); if ($key) { Write-Host 'Registry accessible'; Write-Host 'Subkeys count:' $key.SubKeyCount } else { Write-Host 'Cannot access registry' }" 2>nul
if %errorLevel% neq 0 (
    echo [FAIL] Cannot access software registry
) else (
    echo [OK] Software registry accessible
)

echo.
echo [Test 4] Testing Python/psutil availability...
python --version 2>nul
if %errorLevel% neq 0 (
    echo [INFO] Python not in PATH (normal for EXE version)
) else (
    echo [INFO] Python is available
    python -c "import psutil; print('[OK] psutil version:', psutil.__version__)" 2>nul
)

echo.
echo [Test 5] Checking Agent process...
tasklist | findstr /I "Z-View.exe CMDB-Agent.exe" >nul 2>&1
if %errorLevel% equ 0 (
    echo [OK] Z-View agent process is running
    tasklist /FI "IMAGENAME eq Z-View.exe" /V
    tasklist /FI "IMAGENAME eq CMDB-Agent.exe" /V
) else (
    echo [INFO] Z-View.exe not found, checking Python...
    tasklist | findstr /I "python.exe" >nul 2>&1
    if %errorLevel% equ 0 (
        echo [INFO] Python process found (may be running Agent)
    ) else (
        echo [FAIL] No Agent process found
    )
)

echo.
echo [Test 6] Checking backend scheduled task...
schtasks /query /tn "CMDB Agent Backend" >nul 2>&1
if %errorLevel% equ 0 (
    echo [OK] Backend scheduled task exists
    schtasks /query /tn "CMDB Agent Backend" /v /fo list
) else (
    echo [INFO] Backend scheduled task not found
)

echo.
echo [Test 7] Testing Agent API connectivity...
curl -s http://localhost:9000 2>nul
if %errorLevel% neq 0 (
    echo [FAIL] Cannot connect to Agent API on port 9000
    echo [INFO] Is the Agent running?
) else (
echo [OK] Agent API is responding
)

echo.
echo [Test 8] Checking port 9000 owner...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$line = netstat -ano -p tcp | Select-String 'LISTENING' | Select-String ':9000' | Select-Object -First 1; if ($line) { $parts = ($line.ToString() -split '\s+') | Where-Object { $_ }; $pid = $parts[-1]; Write-Host '[OK] Listening PID:' $pid; try { $proc = Get-Process -Id $pid -ErrorAction Stop; Write-Host '[OK] Process:' $proc.ProcessName } catch {} } else { Write-Host '[INFO] Port 9000 is not listening' }" 2>nul

echo.
echo [Test 9] Checking OS version...
ver
wmic os get Caption,Version 2>nul

echo.
echo ============================================
echo Diagnostic Complete
echo ============================================
echo.
echo If tests fail, possible solutions:
echo 1. Run Agent as Administrator
echo 2. Check PowerShell/WMI availability on the client
echo 3. Reinstall Z-View using install.bat as Administrator
echo.
pause
