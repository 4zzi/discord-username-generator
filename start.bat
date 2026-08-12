@echo off
set "SCRIPT_DIR=%~dp0"
set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
wt -d "%SCRIPT_DIR%" cmd /k "title - AURORA  Username Generator - && python gen.py"
