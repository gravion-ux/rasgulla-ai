@echo off
setlocal
set "APP_DIR=%~dp0"
start "Rasgulla" /D "%APP_DIR%" "%APP_DIR%.venv\Scripts\pythonw.exe" "%APP_DIR%rasgulla.py"
