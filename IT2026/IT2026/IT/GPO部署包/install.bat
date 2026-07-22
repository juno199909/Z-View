@echo off
setlocal EnableExtensions
title Z-View Installer

echo.
echo ============================================
echo    Z-View Installer
echo ============================================
echo.

call "%~dp0deploy.bat" --embedded
set "DEPLOY_EXIT=%errorLevel%"

echo.
if "%DEPLOY_EXIT%"=="0" (
    color 0A
    echo Installation completed successfully.
) else (
    color 0C
    echo Installation failed.
    echo Review log: C:\Windows\Temp\cmdb-agent-deploy.log
)

echo.
pause
exit /b %DEPLOY_EXIT%
