@echo off
chcp 65001 >nul 2>&1
title MULTI_ASSETS - Gestion de la Tâche Planifiée
set "TASK_NAME=MULTI_ASSETS"
set "LOG_DIR=C:\Users\averr\MULTI_ASSETS\code\logs"

:MENU
cls
echo.
echo ╔══════════════════════════════════════════════════════════════╗
echo ║          MULTI_ASSETS - Gestion Tâche Planifiée             ║
echo ╠══════════════════════════════════════════════════════════════╣
echo ║  1. Voir le statut de la tâche                              ║
echo ║  2. Démarrer la tâche (arrière-plan)                        ║
echo ║  3. Arrêter la tâche                                        ║
echo ║  4. Voir les dernières lignes du log                        ║
echo ║  5. Suivre le log en temps réel                             ║
echo ║  6. Lancer en mode console (visible, Ctrl+C pour arrêter)   ║
echo ║  7. Ouvrir le Planificateur de tâches Windows               ║
echo ║  8. Quitter                                                  ║
echo ╚══════════════════════════════════════════════════════════════╝
echo.
set /p choice="Choix : "

if "%choice%"=="1" goto STATUS
if "%choice%"=="2" goto START
if "%choice%"=="3" goto STOP
if "%choice%"=="4" goto LOG
if "%choice%"=="5" goto TAIL
if "%choice%"=="6" goto CONSOLE
if "%choice%"=="7" goto TASKSCHD
if "%choice%"=="8" exit /b
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
"C:\Users\averr\MULTI_ASSETS\.venv\Scripts\python.exe" -B "C:\Users\averr\MULTI_ASSETS\code\src\MULTI_SYMBOLS.py"
echo.
pause
goto MENU

:TASKSCHD
start taskschd.msc
goto MENU
