@echo off
chcp 65001 >nul 2>&1
title MULTI_ASSETS_KRAKEN - Installation Tache Planifiee
echo.
echo +--------------------------------------------------------------+
echo ^|     Installation de la tache planifiee MULTI_ASSETS_KRAKEN  ^|
echo +--------------------------------------------------------------+
echo.

:: -- Verifier les droits admin --
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] Droits administrateur requis. Relance en tant qu'admin...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

:: -- Variables --
set "TASK_NAME=MULTI_ASSETS_KRAKEN"
set "PROJECT_DIR=C:\Users\averr\MULTI_ASSETS"
set "SCRIPT_DIR=C:\Users\averr\MULTI_ASSETS"
set "POWERSHELL_EXE=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
set "START_SAFE_SCRIPT=C:\Users\averr\MULTI_ASSETS\start_safe_kraken.ps1"
set "SCRIPT_PATH=C:\Users\averr\MULTI_ASSETS\code\src\KRAKEN_SYMBOLS.py"
set "LOG_DIR=C:\Users\averr\MULTI_ASSETS\code\logs"

:: -- Creer le dossier logs --
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

:: -- Supprimer l'ancienne tache si elle existe --
schtasks /query /tn "%TASK_NAME%" >nul 2>&1
if %errorlevel% equ 0 (
    echo [*] Suppression de l'ancienne tache Kraken...
    schtasks /delete /tn "%TASK_NAME%" /f >nul 2>&1
)

:: -- Creer le fichier XML de la tache planifiee --
echo [*] Creation de la tache planifiee Kraken...

(
echo ^<?xml version="1.0" encoding="UTF-16"?^>
echo ^<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task"^>
echo   ^<RegistrationInfo^>
echo     ^<Description^>Bot de trading crypto Kraken Pro Spot H24 7/7^</Description^>
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
echo       ^<Command^>%POWERSHELL_EXE%^</Command^>
echo       ^<Arguments^>-NoProfile -ExecutionPolicy Bypass -File "%START_SAFE_SCRIPT%" -Mode hidden^</Arguments^>
echo       ^<WorkingDirectory^>%SCRIPT_DIR%^</WorkingDirectory^>
echo     ^</Exec^>
echo   ^</Actions^>
echo ^</Task^>
) > "%TEMP%\multi_assets_kraken_task.xml"

:: -- Importer la tache --
schtasks /create /tn "%TASK_NAME%" /xml "%TEMP%\multi_assets_kraken_task.xml" /f
if %errorlevel% neq 0 (
    echo [ERREUR] Impossible de creer la tache planifiee Kraken !
    pause
    exit /b 1
)

:: -- Demarrer la tache immediatement --
echo.
echo [*] Demarrage de la tache Kraken...
schtasks /run /tn "%TASK_NAME%"

echo.
echo +--------------------------------------------------------------+
echo ^|                    Installation reussie !                   ^|
echo +--------------------------------------------------------------+
echo ^|  Tache      : MULTI_ASSETS_KRAKEN                            ^|
echo ^|  Bot        : KRAKEN_SYMBOLS.py                               ^|
echo ^|  Logs       : C:\Users\averr\MULTI_ASSETS\code\logs\          ^|
echo ^|  Dashboard  : http://127.0.0.1:8084/dashboard                 ^|
echo ^|                                                              ^|
echo ^|  La tache demarre a chaque connexion Windows et redemarre    ^|
echo ^|  automatiquement en cas d'erreur.                             ^|
echo +--------------------------------------------------------------+
echo.
del "%TEMP%\multi_assets_kraken_task.xml" >nul 2>&1
pause
