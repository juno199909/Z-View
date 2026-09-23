@echo off
setlocal EnableExtensions
title Z-View Diagnostic Tool

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0diagnostic.ps1"
set "EXIT_CODE=%ERRORLEVEL%"

echo.
pause
exit /b %EXIT_CODE%
