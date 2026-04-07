@echo off
chcp 65001 >nul 2>&1
title MULTI_ASSETS — Gestion des tâches
setlocal

set "TASK_NAME=MULTI_ASSETS"
set "LOG_DIR=C:\Users\averr\MULTI_ASSETS\code\logs"
set "PROJECT_DIR=C:\Users\averr\MULTI_ASSETS"
set "PYTHON_EXE=C:\Users\averr\MULTI_ASSETS\.venv\Scripts\python.exe"
set "PYTHONW_EXE=C:\Users\averr\MULTI_ASSETS\.venv\Scripts\pythonw.exe"
set "SCRIPT=C:\Users\averr\MULTI_ASSETS\code\src\MULTI_SYMBOLS.py"

:MENU
cls
echo.
echo ╔══════════════════════════════════════════════════════════════╗
echo ║           MULTI_ASSETS — Gestion des tâches                 ║
echo ╚══════════════════════════════════════════════════════════════╝
echo.
echo   1. Voir le statut de la tâche
echo   2. Démarrer la tâche (arrière-plan)
echo   3. Arrêter la tâche
echo   4. Voir les dernières lignes du log
echo   5. Suivre le log en temps réel (Ctrl+C pour sortir)
echo   6. Lancer en mode console (même fenêtre — bloquant)
echo   7. Lancer le bot en nouvelle fenêtre
echo   8. Ouvrir le Planificateur de tâches Windows
echo   9. Quitter
echo  10. Ouvrir le dashboard (métriques live)
echo.
set /p CHOICE=Votre choix [1-10] :

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
goto MENU

:STATUS
echo.
schtasks /query /tn "%TASK_NAME%" /v /fo LIST 2>nul
if %errorlevel% neq 0 echo [!] La tâche n'existe pas. Lancez install_task.bat.
echo.
pause
goto MENU

:START
echo.
schtasks /run /tn "%TASK_NAME%" 2>nul
if %errorlevel% equ 0 (
    echo [OK] Tâche démarrée en arrière-plan.
) else (
    echo [!] Impossible de démarrer la tâche. Lancez install_task.bat d'abord.
)
echo.
pause
goto MENU

:STOP
echo.
schtasks /end /tn "%TASK_NAME%" 2>nul
if %errorlevel% equ 0 (
    echo [OK] Tâche arrêtée.
) else (
    echo [!] Impossible d'arrêter la tâche (peut-être déjà arrêtée).
)
echo.
pause
goto MENU

:LOG
echo.
echo === 50 dernières lignes du log ===
echo.
for /f "delims=" %%F in ('dir /b /o-d "%LOG_DIR%\*.log" 2^>nul') do (
    echo [Fichier: %%F]
    powershell -Command "Get-Content '%LOG_DIR%\%%F' -Tail 50 -ErrorAction SilentlyContinue"
    goto :LOG_DONE
)
echo [!] Aucun fichier log trouvé dans %LOG_DIR%
:LOG_DONE
echo.
pause
goto MENU

:TAIL
echo.
echo === Suivi en temps réel (Ctrl+C pour arrêter) ===
echo.
for /f "delims=" %%F in ('dir /b /o-d "%LOG_DIR%\*.log" 2^>nul') do (
    powershell -Command "Get-Content '%LOG_DIR%\%%F' -Tail 10 -Wait -ErrorAction SilentlyContinue"
    goto :TAIL_DONE
)
echo [!] Aucun fichier log trouvé dans %LOG_DIR%
:TAIL_DONE
echo.
pause
goto MENU

:CONSOLE
echo.
echo === Lancement en mode console (Ctrl+C pour arrêter) ===
echo.
"%PYTHON_EXE%" -B "%SCRIPT%"
echo.
pause
goto MENU

:OPT_CONSOLE_WIN
echo.
echo [*] Lancement du bot dans une nouvelle fenetre...
echo     Fermez la fenetre "MULTI_ASSETS Bot" pour arreter le bot.
start "MULTI_ASSETS Bot" cmd /k "cd /d "%PROJECT_DIR%" && "%PYTHON_EXE%" -B "%SCRIPT%""
echo [OK] Bot demarre. Vous pouvez maintenant ouvrir le dashboard (option 10).
timeout /t 2 >nul
goto MENU

:OPT_DASHBOARD
echo.
echo [*] Arret de l'ancien serveur dashboard si existant...
for /f "tokens=5" %%p in ('netstat -ano 2^>nul ^| findstr ":8082 " ^| findstr "LISTENING"') do (
    taskkill /PID %%p /F >nul 2>&1
)
timeout /t 1 >nul
echo [*] Lancement du dashboard web (http://127.0.0.1:8082/dashboard)...
wscript //nologo "C:\Users\averr\MULTI_ASSETS\code\scripts\launch_dashboard.vbs"
timeout /t 2 >nul
start "" "http://127.0.0.1:8082/dashboard"
echo [OK] Dashboard ouvert dans le navigateur.
timeout /t 2 >nul
goto MENU

:TASKSCHD
start taskschd.msc
goto MENU

:END
endlocal
