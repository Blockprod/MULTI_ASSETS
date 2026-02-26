@echo off
REM Kill all python.exe processes running MULTI_SYMBOLS.py (forcibly)
REM Use with caution: this will kill ALL python processes running this script, including manual and service instances

set SCRIPT_NAME=MULTI_SYMBOLS.py

echo Recherche des processus Python lancant %SCRIPT_NAME% ...
for /f "tokens=2 delims=," %%a in ('wmic process where "name='python.exe' and commandline like '%%%SCRIPT_NAME%%%"" get ProcessId^,CommandLine /format:csv ^| findstr %SCRIPT_NAME%') do (
    echo   Arret du processus PID=%%a
    taskkill /PID %%a /F
)
echo.
echo Tous les processus python.exe lies a %SCRIPT_NAME% ont ete termines (si trouves).
pause
