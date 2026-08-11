@echo off
taskkill /F /IM tor.exe >nul 2>&1
if %errorlevel% == 0 (
    echo all instances killed
) else (
    echo no instances running
)
pause