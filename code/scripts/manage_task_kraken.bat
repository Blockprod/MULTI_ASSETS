@echo off
chcp 65001 >nul 2>&1
title MULTI_ASSETS_KRAKEN - Gestion des taches
setlocal

set "TASK_NAME=MULTI_ASSETS_KRAKEN"
set "LOG_DIR=C:\Users\averr\MULTI_ASSETS\code\src\logs"
set "LOG_FILE=C:\Users\averr\MULTI_ASSETS\code\src\logs\kraken_trading_bot.log"
set "PROJECT_DIR=C:\Users\averr\MULTI_ASSETS"
set "PYTHON_EXE=C:\Users\averr\MULTI_ASSETS\.venv\Scripts\python.exe"
set "PYTHONW_EXE=C:\Users\averr\MULTI_ASSETS\.venv\Scripts\pythonw.exe"
set "SCRIPT=C:\Users\averr\MULTI_ASSETS\code\src\KRAKEN_SYMBOLS.py"
set "START_SAFE_SCRIPT=C:\Users\averr\MULTI_ASSETS\start_safe_kraken.ps1"
set "BOT_WAIT_SCRIPT=C:\Users\averr\MULTI_ASSETS\code\scripts\wait_for_bot_ready.py"
set "LOCK_FILE=C:\Users\averr\MULTI_ASSETS\.running_kraken.lock"
set "HEARTBEAT_FILE=C:\Users\averr\MULTI_ASSETS\code\src\kraken_bot\states\heartbeat_kraken.json"
set "HEARTBEAT_MAX_AGE=180"
set "DASHBOARD_URL=http://127.0.0.1:8084/dashboard"
set "DASHBOARD_API_URL=http://127.0.0.1:8084/api/data"
set "DASHBOARD_WAIT_SCRIPT=C:\Users\averr\MULTI_ASSETS\code\scripts\wait_for_dashboard_ready.py"
set "NONINTERACTIVE=0"

if not "%~1"=="" (
    set "NONINTERACTIVE=1"
    set "CHOICE=%~1"
    goto ROUTE_CHOICE
)

:MENU
cls
echo.
echo +--------------------------------------------------------------+
echo ^|           MULTI_ASSETS_KRAKEN - Gestion des taches          ^|
echo +--------------------------------------------------------------+
echo.
echo   1. Voir le statut de la tache
echo   2. Demarrer la tache Kraken (arriere-plan)
echo   3. Arreter la tache Kraken
echo   4. Voir les dernieres lignes du log Kraken
echo   5. Suivre le log Kraken en temps reel (Ctrl+C pour sortir)
echo   6. Lancer Kraken en mode console (meme fenetre - bloquant)
echo   7. Lancer Kraken en nouvelle fenetre
echo  11. Forcer le redemarrage Kraken en console
echo   8. Ouvrir le Planificateur de taches Windows
echo   9. Quitter
echo  10. Ouvrir le dashboard Kraken (port 8084)
echo.
set "CHOICE="
set /p CHOICE=Votre choix [1-11] :

:ROUTE_CHOICE
if "%CHOICE%"=="1" goto STATUS
if "%CHOICE%"=="2" goto START
if "%CHOICE%"=="3" goto STOP
if "%CHOICE%"=="4" goto LOG
if "%CHOICE%"=="5" goto TAIL
if "%CHOICE%"=="6" goto CONSOLE
if "%CHOICE%"=="7" goto OPT_CONSOLE_WIN
if "%CHOICE%"=="8" goto TASKSCHD
if "%CHOICE%"=="9" goto END
if "%CHOICE%"=="10" call :OPEN_DASHBOARD
if "%CHOICE%"=="11" goto OPT_RESTART_CONSOLE_WIN
if "%NONINTERACTIVE%"=="1" goto END
goto MENU

:STATUS
echo.
schtasks /query /tn "%TASK_NAME%" /v /fo LIST 2>nul
if errorlevel 1 echo [!] La tache n'existe pas. Lancez install_task_kraken.bat.
call :RUNTIME_STATUS
echo.
if "%NONINTERACTIVE%"=="1" goto END
pause
goto MENU

:START
echo.
schtasks /run /tn "%TASK_NAME%" 2>nul
if errorlevel 1 (
    echo [!] Impossible de demarrer la tache. Lancez install_task_kraken.bat d'abord.
) else (
    echo [OK] Demande de demarrage envoyee a la tache Kraken.
    echo [*] Attente du heartbeat Kraken...
    "%PYTHON_EXE%" "%BOT_WAIT_SCRIPT%" 120 --lock "%LOCK_FILE%" --heartbeat "%HEARTBEAT_FILE%" --max-age %HEARTBEAT_MAX_AGE%
    if errorlevel 1 (
        echo [!] La tache a ete lancee, mais le bot Kraken n'a pas confirme un heartbeat pret.
        call :RUNTIME_STATUS
    ) else (
        echo [OK] Bot Kraken pret en arriere-plan.
    )
)
echo.
if "%NONINTERACTIVE%"=="1" goto END
pause
goto MENU

:STOP
echo.
schtasks /end /tn "%TASK_NAME%" 2>nul
if errorlevel 1 (
    echo [!] Tache Kraken deja arretee ou introuvable.
) else (
    echo [OK] Demande d'arret envoyee a la tache Kraken.
)
call :STOP_KRAKEN_PROCESSES
if exist "%LOCK_FILE%" del /f /q "%LOCK_FILE%" >nul 2>&1
call :RUNTIME_STATUS
echo.
if "%NONINTERACTIVE%"=="1" goto END
pause
goto MENU

:LOG
echo.
echo === 50 dernieres lignes du log Kraken ===
echo.
if exist "%LOG_FILE%" (
    powershell -Command "Get-Content '%LOG_FILE%' -Tail 50 -ErrorAction SilentlyContinue"
) else (
    echo [!] Aucun log kraken_trading_bot.log trouve dans %LOG_DIR%
    echo     Si le bot vient d'etre lance, attendez le premier cycle.
)
echo.
if "%NONINTERACTIVE%"=="1" goto END
pause
goto MENU

:TAIL
echo.
echo === Suivi Kraken en temps reel (Ctrl+C pour arreter) ===
echo.
if exist "%LOG_FILE%" (
    powershell -Command "Get-Content '%LOG_FILE%' -Tail 10 -Wait -ErrorAction SilentlyContinue"
) else (
    echo [!] Aucun log kraken_trading_bot.log trouve.
)
echo.
if "%NONINTERACTIVE%"=="1" goto END
pause
goto MENU

:CONSOLE
echo.
echo === Lancement Kraken en mode console (Ctrl+C pour arreter) ===
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%START_SAFE_SCRIPT%" -Mode console
echo.
if "%NONINTERACTIVE%"=="1" goto END
pause
goto MENU

:OPT_CONSOLE_WIN
echo.
echo [*] Lancement du bot Kraken dans une nouvelle fenetre...
echo     La fenetre affichera les panels Rich en direct.
echo     Si un bot Kraken est deja actif, il ne sera PAS redemarre.
start "MULTI_ASSETS Kraken Bot" powershell -NoProfile -NoExit -ExecutionPolicy Bypass -Command "& 'C:\Users\averr\MULTI_ASSETS\start_safe_kraken.ps1' -Mode console"
echo [*] Attente du heartbeat Kraken...
"%PYTHON_EXE%" "%BOT_WAIT_SCRIPT%" 120 --lock "%LOCK_FILE%" --heartbeat "%HEARTBEAT_FILE%" --max-age %HEARTBEAT_MAX_AGE%
if errorlevel 1 (
    echo [!] Fenetre console lancee, mais Kraken n'a pas signale un heartbeat pret a temps.
    echo     Verifiez la nouvelle fenetre et le fichier code\src\kraken_bot\states\heartbeat_kraken.json.
) else (
    echo [OK] Bot Kraken pret en mode console.
)
call :SLEEP_SECONDS 2
if "%NONINTERACTIVE%"=="1" goto END
goto MENU

:OPT_RESTART_CONSOLE_WIN
echo.
echo [*] Redemarrage force du bot Kraken dans une nouvelle fenetre...
echo     Utiliser cette option seulement si vous voulez relancer un bot Kraken deja actif.
start "MULTI_ASSETS Kraken Bot" powershell -NoProfile -NoExit -ExecutionPolicy Bypass -Command "& 'C:\Users\averr\MULTI_ASSETS\start_safe_kraken.ps1' -Mode console -RestartIfRunning"
echo [*] Attente du heartbeat Kraken apres redemarrage...
"%PYTHON_EXE%" "%BOT_WAIT_SCRIPT%" 120 --lock "%LOCK_FILE%" --heartbeat "%HEARTBEAT_FILE%" --max-age %HEARTBEAT_MAX_AGE%
if errorlevel 1 (
    echo [!] Nouvelle fenetre lancee, mais Kraken n'a pas signale un heartbeat pret a temps.
    echo     Verifiez la nouvelle fenetre et le fichier code\src\kraken_bot\states\heartbeat_kraken.json.
) else (
    echo [OK] Bot Kraken pret apres redemarrage force en mode console.
)
call :SLEEP_SECONDS 2
if "%NONINTERACTIVE%"=="1" goto END
goto MENU

:TASKSCHD
start taskschd.msc
if "%NONINTERACTIVE%"=="1" goto END
goto MENU

:END
endlocal
exit /b 0

:STOP_KRAKEN_PROCESSES
powershell -NoProfile -ExecutionPolicy Bypass -Command "$targets = Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^pythonw?\.exe$' -and $_.CommandLine -and $_.CommandLine -like '*\code\src\KRAKEN_SYMBOLS.py*' }; if ($targets) { $targets | ForEach-Object { Write-Host ('[*] Arret process Kraken PID=' + $_.ProcessId); Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } } else { Write-Host '[OK] Aucun process bot Kraken actif.' }"
exit /b 0

:RUNTIME_STATUS
echo.
echo === Statut runtime Kraken ===
powershell -NoProfile -ExecutionPolicy Bypass -Command "$lock='%LOCK_FILE%'; $hb='%HEARTBEAT_FILE%'; $procs=Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^pythonw?\.exe$' -and $_.CommandLine -and $_.CommandLine -like '*\code\src\KRAKEN_SYMBOLS.py*' }; if ($procs) { $procs | Select-Object ProcessId,Name,CommandLine | Format-List } else { Write-Host '[OK] Aucun process KRAKEN_SYMBOLS.py actif.' }; if (Test-Path $lock) { Write-Host ('Lock: ' + (Get-Content $lock -ErrorAction SilentlyContinue | Select-Object -First 1)) } else { Write-Host 'Lock: absent' }; if (Test-Path $hb) { try { $j=Get-Content $hb -Raw -ErrorAction Stop | ConvertFrom-Json; $ts=[string]$j.timestamp; $age='n/a'; if ($ts) { $age=[math]::Round(([datetimeoffset]::UtcNow - [datetimeoffset]::Parse($ts).ToUniversalTime()).TotalSeconds,1) }; Write-Host ('Heartbeat: pid=' + $j.pid + ' age=' + $age + 's loop=' + $j.loop_counter + ' mode=' + $j.circuit_mode) } catch { Write-Host ('Heartbeat: lecture impossible - ' + $_.Exception.Message) } } else { Write-Host 'Heartbeat: absent' }"
exit /b 0

:OPEN_DASHBOARD
echo.
echo [*] Arret de l'ancien serveur dashboard Kraken si existant...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^pythonw?\.exe$' -and $_.CommandLine -and $_.CommandLine -like '*kraken_dashboard_server.py*' } | ForEach-Object { Write-Host ('[*] Arret dashboard PID=' + $_.ProcessId); Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
call :SLEEP_SECONDS 1
echo [*] Lancement du dashboard Kraken (%DASHBOARD_URL%)...
wscript //nologo "C:\Users\averr\MULTI_ASSETS\code\scripts\launch_kraken_dashboard.vbs"
echo [*] Attente du serveur dashboard Kraken...
"%PYTHON_EXE%" "%DASHBOARD_WAIT_SCRIPT%" "%DASHBOARD_API_URL%" 20
if errorlevel 1 (
    echo [!] Dashboard Kraken lance mais l'API n'a pas repondu a temps.
    echo     Verifiez le process pythonw et le port 8084.
) else (
    start "" "%DASHBOARD_URL%"
    echo [OK] Dashboard Kraken ouvert dans le navigateur.
)
call :SLEEP_SECONDS 2
exit /b 0

:SLEEP_SECONDS
if "%NONINTERACTIVE%"=="1" (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Sleep -Seconds %~1"
) else (
    timeout /t %~1 >nul
)
exit /b 0
