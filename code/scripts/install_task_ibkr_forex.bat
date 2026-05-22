@echo off
chcp 65001 >nul 2>&1
title IBKR_FOREX - Installation Tache Planifiee
echo.
echo +--------------------------------------------------------------+
echo ^|     Installation de la tache planifiee IBKR_FOREX           ^|
echo +--------------------------------------------------------------+
echo.

:: -- Verifier droits admin --
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] Droits administrateur requis. Relance en tant qu'admin...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

:: -- Variables --
set "TASK_NAME=IBKR_FOREX"
set "SCRIPT_DIR=C:\Users\averr\MULTI_ASSETS\code\src\ibkr"
set "PYTHON_EXE=C:\Users\averr\MULTI_ASSETS\.venv\Scripts\pythonw.exe"
set "SCRIPT_PATH=C:\Users\averr\MULTI_ASSETS\code\src\ibkr\IBKR_FOREX.py"
set "LOG_DIR=C:\Users\averr\MULTI_ASSETS\code\logs"

:: -- Creer le dossier logs --
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

:: -- Supprimer ancienne tache si elle existe --
schtasks /query /tn "%TASK_NAME%" >nul 2>&1
if %errorlevel% equ 0 (
    echo [*] Suppression ancienne tache...
    schtasks /delete /tn "%TASK_NAME%" /f >nul 2>&1
)

:: -- Creer le XML de la tache --
echo [*] Creation de la tache planifiee IBKR_FOREX...
(
echo ^<?xml version="1.0" encoding="UTF-16"?^>
echo ^<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task"^>
echo   ^<RegistrationInfo^>
echo     ^<Description^>Bot IBKR Forex EUR/USD EUR/GBP - Paper Trading H24/7^</Description^>
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
) > "%TEMP%\ibkr_forex_task.xml"

:: -- Importer la tache --
schtasks /create /tn "%TASK_NAME%" /xml "%TEMP%\ibkr_forex_task.xml" /f
if %errorlevel% neq 0 (
    echo [ERREUR] Impossible de creer la tache !
    pause
    exit /b 1
)

:: -- Demarrer immediatement --
echo.
echo [*] Demarrage de la tache IBKR_FOREX...
schtasks /run /tn "%TASK_NAME%"
echo.
echo +--------------------------------------------------------------+
echo ^|              Installation reussie !                          ^|
echo ^|  La tache IBKR_FOREX est active et demarrera a la connexion. ^|
echo +--------------------------------------------------------------+
echo.
pause
