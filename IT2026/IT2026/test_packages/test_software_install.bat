@echo off
echo ========================================
echo Test Software Installation
echo ========================================
echo.
echo Installation started at %TIME%
echo Installing to: %PROGRAMFILES%\TestSoftware
echo.

REM Create installation directory
mkdir "%PROGRAMFILES%\TestSoftware" 2>nul

REM Create test files
echo Test Application > "%PROGRAMFILES%\TestSoftware\readme.txt"
echo Version: 1.0.0 >> "%PROGRAMFILES%\TestSoftware\readme.txt"
echo Installed: %DATE% %TIME% >> "%PROGRAMFILES%\TestSoftware\readme.txt"

echo.
echo [SUCCESS] Installation completed successfully!
echo.
echo Files created:
echo   %PROGRAMFILES%\TestSoftware\readme.txt
echo.
echo Installation finished at %TIME%
echo ========================================

exit /b 0
