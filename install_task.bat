@echo off
chcp 65001 >nul 2>&1
title MULTI_ASSETS - Installation Tâche Planifiée
echo.
echo ╔══════════════════════════════════════════════════════════════╗
echo ║     Installation de la tâche planifiée MULTI_ASSETS         ║
echo ╚══════════════════════════════════════════════════════════════╝
echo.

:: ── Vérifier les droits admin ──
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] Droits administrateur requis. Relance en tant qu'admin...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

:: ── Variables ──
set "TASK_NAME=MULTI_ASSETS"
set "SCRIPT_DIR=C:\Users\averr\MULTI_ASSETS\code\src"
set "PYTHON_EXE=C:\Users\averr\MULTI_ASSETS\.venv\Scripts\pythonw.exe"
set "SCRIPT_PATH=C:\Users\averr\MULTI_ASSETS\code\src\MULTI_SYMBOLS.py"
set "LOG_DIR=C:\Users\averr\MULTI_ASSETS\code\logs"

:: ── Créer le dossier logs ──
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

:: ── Supprimer l'ancienne tâche si elle existe ──
schtasks /query /tn "%TASK_NAME%" >nul 2>&1
if %errorlevel% equ 0 (
    echo [*] Suppression de l'ancienne tâche...
    schtasks /delete /tn "%TASK_NAME%" /f >nul 2>&1
)

:: ── Créer le fichier XML de la tâche planifiée ──
echo [*] Création de la tâche planifiée...

(
echo ^<?xml version="1.0" encoding="UTF-16"?^>
echo ^<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task"^>
echo   ^<RegistrationInfo^>
echo     ^<Description^>Bot de trading crypto multi-assets H24 7/7^</Description^>
echo   ^</RegistrationInfo^>
echo   ^<Triggers^>
echo     ^<LogonTrigger^>
echo       ^<Enabled^>true^</Enabled^>
echo     ^</LogonTrigger^>
echo   ^</Triggers^>
echo   ^<Principals^>
echo     ^<Principal id="Author"^>
echo       ^<LogonType^>InteractiveToken^</LogonType^>
echo       ^<RunLevel^>LeastPrivilege^</RunLevel^>
echo     ^</Principal^>
echo   ^</Principals^>
echo   ^<Settings^>
echo     ^<MultipleInstancesPolicy^>IgnoreNew^</MultipleInstancesPolicy^>
echo     ^<DisallowStartIfOnBatteries^>false^</DisallowStartIfOnBatteries^>
echo     ^<StopIfGoingOnBatteries^>false^</StopIfGoingOnBatteries^>
echo     ^<AllowHardTerminate^>true^</AllowHardTerminate^>
echo     ^<StartWhenAvailable^>true^</StartWhenAvailable^>
echo     ^<RunOnlyIfNetworkAvailable^>true^</RunOnlyIfNetworkAvailable^>
echo     ^<AllowStartOnDemand^>true^</AllowStartOnDemand^>
echo     ^<Enabled^>true^</Enabled^>
echo     ^<Hidden^>false^</Hidden^>
echo     ^<RunOnlyIfIdle^>false^</RunOnlyIfIdle^>
echo     ^<WakeToRun^>false^</WakeToRun^>
echo     ^<ExecutionTimeLimit^>PT0S^</ExecutionTimeLimit^>
echo     ^<Priority^>7^</Priority^>
echo     ^<RestartOnFailure^>
echo       ^<Interval^>PT1M^</Interval^>
echo       ^<Count^>999^</Count^>
echo     ^</RestartOnFailure^>
echo   ^</Settings^>
echo   ^<Actions Context="Author"^>
echo     ^<Exec^>
echo       ^<Command^>%PYTHON_EXE%^</Command^>
echo       ^<Arguments^>-B %SCRIPT_PATH%^</Arguments^>
echo       ^<WorkingDirectory^>%SCRIPT_DIR%^</WorkingDirectory^>
echo     ^</Exec^>
echo   ^</Actions^>
echo ^</Task^>
) > "%TEMP%\multi_assets_task.xml"

:: ── Importer la tâche ──
schtasks /create /tn "%TASK_NAME%" /xml "%TEMP%\multi_assets_task.xml" /f
if %errorlevel% neq 0 (
    echo [ERREUR] Impossible de créer la tâche planifiée !
    pause
    exit /b 1
)

:: ── Démarrer la tâche immédiatement ──
echo.
echo [*] Démarrage de la tâche...
schtasks /run /tn "%TASK_NAME%"

echo.
echo ╔══════════════════════════════════════════════════════════════╗
echo ║                    Installation réussie !                    ║
echo ╠══════════════════════════════════════════════════════════════╣
echo ║  La tâche MULTI_ASSETS est configurée pour :                ║
echo ║   • Se lancer automatiquement à chaque connexion Windows    ║
echo ║   • Redémarrer automatiquement en cas d'erreur (max 999x)   ║
echo ║   • Fonctionner sur batterie et en réseau uniquement        ║
echo ║   • Tourner en arrière-plan (pythonw.exe, pas de fenêtre)   ║
echo ║   • Trading planifié toutes les 2 min via schedule          ║
echo ║                                                              ║
echo ║  Logs : C:\Users\averr\MULTI_ASSETS\code\logs\              ║
echo ╚══════════════════════════════════════════════════════════════╝
echo.
del "%TEMP%\multi_assets_task.xml" >nul 2>&1
pause
