@echo off
chcp 65001 >nul 2>&1
title IBKR Forex - Gestion des taches
setlocal

set "TASK_NAME=IBKR_FOREX"
set "LOG_DIR=C:\Users\averr\MULTI_ASSETS\code\logs"
set "PROJECT_DIR=C:\Users\averr\MULTI_ASSETS"
set "PYTHON_EXE=C:\Users\averr\MULTI_ASSETS\.venv\Scripts\python.exe"
set "PYTHONW_EXE=C:\Users\averr\MULTI_ASSETS\.venv\Scripts\pythonw.exe"
set "SCRIPT=C:\Users\averr\MULTI_ASSETS\code\src\ibkr\IBKR_FOREX.py"
set "DASHBOARD_URL=http://127.0.0.1:8083/dashboard"
set "DASHBOARD_API_URL=http://127.0.0.1:8083/api/data"
set "DASHBOARD_WAIT_SCRIPT=C:\Users\averr\MULTI_ASSETS\code\scripts\wait_for_dashboard_ready.py"

:MENU
cls
echo.
echo +--------------------------------------------------------------+
echo ^|           IBKR Forex - Gestion des taches                   ^|
echo +--------------------------------------------------------------+
echo.
echo   1. Voir le statut de la tache
echo   2. Demarrer la tache (arriere-plan)
echo   3. Arreter la tache
echo   4. Voir les dernieres lignes du log
echo   5. Suivre le log en temps reel (Ctrl+C pour sortir)
echo   6. Lancer en mode console (meme fenetre -- bloquant)
echo   7. Lancer le bot en nouvelle fenetre
echo  11. Forcer le redemarrage du bot en console
echo   8. Ouvrir le Planificateur de taches Windows
echo   9. Quitter
echo  10. Ouvrir le dashboard IBKR (port 8083)
echo.
set /p CHOICE=Votre choix [1-11] : 

if "%CHOICE%"=="1" goto STATUS
if "%CHOICE%"=="2" goto START
if "%CHOICE%"=="3" goto STOP
if "%CHOICE%"=="4" goto LOG
if "%CHOICE%"=="5" goto TAIL
if "%CHOICE%"=="6" goto CONSOLE
if "%CHOICE%"=="7" goto OPT_CONSOLE_WIN
if "%CHOICE%"=="8" goto TASKSCHD
if "%CHOICE%"=="9" goto END
if "%CHOICE%"=="10" goto OPT_DASHBOARD
if "%CHOICE%"=="11" goto OPT_RESTART_CONSOLE_WIN
goto MENU

:STATUS
echo.
schtasks /query /tn "%TASK_NAME%" /v /fo LIST 2>nul
if %errorlevel% neq 0 echo [!] La tache n existe pas. Lancez install_task_ibkr_forex.bat.
echo.
pause
goto MENU

:START
echo.
schtasks /run /tn "%TASK_NAME%" 2>nul
if %errorlevel% equ 0 (
    echo [OK] Tache IBKR_FOREX demarree en arriere-plan.
) else (
    echo [!] Impossible de demarrer la tache. Lancez install_task_ibkr_forex.bat d abord.
)
echo.
pause
goto MENU

:STOP
echo.
schtasks /end /tn "%TASK_NAME%" 2>nul
if %errorlevel% equ 0 (
    echo [OK] Tache IBKR_FOREX arretee.
) else (
    echo [!] Impossible d arreter la tache (peut-etre deja arretee).
)
echo.
pause
goto MENU

:LOG
echo.
echo === 50 dernieres lignes du log ibkr_forex.log ===
echo.
if exist "%LOG_DIR%\ibkr_forex.log" (
    powershell -Command "Get-Content '%LOG_DIR%\ibkr_forex.log' -Tail 50 -ErrorAction SilentlyContinue"
) else (
    echo [!] Aucun log ibkr_forex.log trouve dans %LOG_DIR%
)
echo.
pause
goto MENU

:TAIL
echo.
echo === Suivi en temps reel (Ctrl+C pour arreter) ===
echo.
if exist "%LOG_DIR%\ibkr_forex.log" (
    powershell -Command "Get-Content '%LOG_DIR%\ibkr_forex.log' -Tail 10 -Wait -ErrorAction SilentlyContinue"
) else (
    echo [!] Aucun log ibkr_forex.log trouve.
)
echo.
pause
goto MENU

:CONSOLE
echo.
echo === Lancement IBKR_FOREX en mode console (Ctrl+C pour arreter) ===
echo.
"%PYTHON_EXE%" -B "%SCRIPT%"
echo.
pause
goto MENU

:OPT_CONSOLE_WIN
echo.
echo [*] Lancement du bot IBKR Forex dans une nouvelle fenetre...
echo     La fenetre affichera les logs en direct.
echo     Si un bot est deja actif, il ne sera PAS redemarre.
start "IBKR Forex Bot" powershell -NoProfile -NoExit -ExecutionPolicy Bypass -Command "& C:\Users\averr\MULTI_ASSETS\.venv\Scripts\python.exe -B C:\Users\averr\MULTI_ASSETS\code\src\ibkr\IBKR_FOREX.py"
echo [OK] Fenetre bot IBKR Forex lancee.
timeout /t 2 >nul
goto MENU

:OPT_RESTART_CONSOLE_WIN
echo.
echo [*] Redemarrage force du bot IBKR Forex dans une nouvelle fenetre...
echo     Utiliser cette option seulement si vous voulez relancer un bot deja actif.
taskkill /F /FI "WINDOWTITLE eq IBKR Forex Bot" >nul 2>&1
timeout /t 2 >nul
start "IBKR Forex Bot" powershell -NoProfile -NoExit -ExecutionPolicy Bypass -Command "& C:\Users\averr\MULTI_ASSETS\.venv\Scripts\python.exe -B C:\Users\averr\MULTI_ASSETS\code\src\ibkr\IBKR_FOREX.py"
echo [OK] Fenetre bot IBKR Forex relancee.
timeout /t 2 >nul
goto MENU

:OPT_DASHBOARD
echo.
echo [*] Arret de l ancien serveur dashboard si existant...
for /f "tokens=5" %%p in ('netstat -ano 2^>nul ^| findstr ":8083 " ^| findstr "LISTENING"') do (
    taskkill /PID %%p /F >nul 2>&1
)
timeout /t 1 >nul
echo [*] Lancement du dashboard IBKR Forex (%DASHBOARD_URL%)...
wscript //nologo "C:\Users\averr\MULTI_ASSETS\code\scripts\launch_ibkr_forex_dashboard.vbs"
echo [*] Attente du serveur dashboard...
"%PYTHON_EXE%" "%DASHBOARD_WAIT_SCRIPT%" "%DASHBOARD_API_URL%" 20
if %errorlevel% equ 0 (
    start "" "%DASHBOARD_URL%"
    echo [OK] Dashboard IBKR Forex ouvert dans le navigateur.
) else (
    echo [!] Dashboard lance mais l API n a pas repondu a temps.
    echo     Verifiez le process pythonw et le port 8083.
)
timeout /t 2 >nul
goto MENU

:TASKSCHD
start taskschd.msc
goto MENU

:END
endlocal