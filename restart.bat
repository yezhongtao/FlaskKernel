@echo off
cd /d "%~dp0"
:loop
tasklist | find /I "oa.exe" >nul
if errorlevel 1 (
    start "" "%~dp0oa.exe"
)
timeout /t 3 /nobreak >nul
goto loop