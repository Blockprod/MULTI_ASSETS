@echo off
setlocal EnableDelayedExpansion

REM === Vérification des droits administrateur ===
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERREUR] Ce script doit etre lance en mode administrateur !
    pause
    exit /b
)

title Monitoring Crypto Trading Bots Services

REM === Configuration des services ===
set "SERVICE1=CryptoBot_MultiAssets"
set "SERVICE2=CryptoBot_NEWSPAPERS_IPO"
set "SERVICE3=CryptoBot_NEWSPAPERS_BTC"
set "SERVICE4=CryptoBot_LTV_SYSTEM"

set "LOG_DIR1=C:\Users\averr\BIBOT\MULTI_ASSETS_BOT\code\src"
set "LOG_DIR2=C:\Users\averr\BIBOT\NEWS PAPERS"
set "LOG_DIR3=C:\Users\averr\BIBOT\NEWS PAPERS"
set "LOG_DIR4=C:\Users\averr\BIBOT\LTV_SYSTEM\code"

:LOOP
cls
echo ==========================================
echo    CRYPTO TRADING BOTS - MONITORING
echo ==========================================
echo.

echo [STATUS DES SERVICES]
echo Bot 1 - Multi Indicators:
sc query "%SERVICE1%" | findstr "STATE"
echo Bot 2 - Newspapers IPO:
sc query "%SERVICE2%" | findstr "STATE"
echo Bot 3 - Newspapers BTC:
sc query "%SERVICE3%" | findstr "STATE"
echo Bot 4 - LTV Alert Monitor:
sc query CryptoBot_LTV_Alert | findstr "STATE"
echo Bot 4 - LTV FastAPI Backend:
sc query CryptoBot_FastAPI | findstr "STATE"
echo Bot 4 - LTV Frontend:
sc query CryptoBot_Frontend | findstr "STATE"
echo.

echo [PROCESSUS PYTHON]
tasklist | findstr /I "python.exe"
echo.

echo [DERNIERS LOGS - 5 LIGNES PAR BOT]
echo --- Bot 1 (Multi Indicators) ---
powershell -Command "Get-Content '%LOG_DIR1%\service.log' -Tail 20 -ErrorAction SilentlyContinue | Select-String 'ERROR|WARNING' | Select-Object -Last 5"
echo --- Bot 2 (IPO) ---
powershell -Command "Get-Content '%LOG_DIR2%\service_ipo.log' -Tail 20 -ErrorAction SilentlyContinue | Select-String 'ERROR|WARNING' | Select-Object -Last 5"
echo --- Bot 3 (BTC) ---
powershell -Command "Get-Content '%LOG_DIR3%\service_btc.log' -Tail 20 -ErrorAction SilentlyContinue | Select-String 'ERROR|WARNING' | Select-Object -Last 5"
echo --- Bot 4 (LTV) ---
echo === LTV System: Alert Monitor ===
echo [stdout]
powershell -Command "Get-Content 'C:\Users\averr\BIBOT\LTV_SYSTEM\backend\ltv_service.log' -Tail 20 -ErrorAction SilentlyContinue | Select-String 'ERROR|WARNING' | Select-Object -Last 5"
echo [stderr]
powershell -Command "Get-Content 'C:\Users\averr\BIBOT\LTV_SYSTEM\backend\ltv_service_error.log' -Tail 20 -ErrorAction SilentlyContinue | Select-String 'ERROR|WARNING' | Select-Object -Last 5"
echo.
echo === LTV System: FastAPI Backend ===
echo [stdout]
powershell -Command "Get-Content 'C:\Users\averr\BIBOT\LTV_SYSTEM\backend\fastapi_service.log' -Tail 20 -ErrorAction SilentlyContinue | Select-String 'ERROR|WARNING' | Select-Object -Last 5"
echo [stderr]
powershell -Command "Get-Content 'C:\Users\averr\BIBOT\LTV_SYSTEM\backend\fastapi_service_error.log' -Tail 20 -ErrorAction SilentlyContinue | Select-String 'ERROR|WARNING' | Select-Object -Last 5"
echo.
echo === LTV System: Frontend ===
echo [stdout]
powershell -Command "Get-Content 'C:\Users\averr\BIBOT\LTV_SYSTEM\frontend\frontend_service.log' -Tail 20 -ErrorAction SilentlyContinue | Select-String 'ERROR|WARNING' | Select-Object -Last 5"
echo [stderr]
powershell -Command "Get-Content 'C:\Users\averr\BIBOT\LTV_SYSTEM\frontend\frontend_service_error.log' -Tail 20 -ErrorAction SilentlyContinue | Select-String 'ERROR|WARNING' | Select-Object -Last 5"
echo.
echo.

echo [ACTIONS DISPONIBLES]
echo 1. Gerer Bot 1 (Multi Indicators)
echo 2. Gerer Bot 2 (Newspapers IPO)
echo 3. Gerer Bot 3 (Newspapers BTC)
echo 4. Gerer Bot 4 (LTV Monitor)
echo 5. Gerer TOUS les bots
echo 6. Actualiser (auto dans 30s)
echo.

choice /c 123456 /t 30 /d 6 /m "Votre choix"

REM Toujours tester errorlevel du plus grand au plus petit
if errorlevel 6 goto LOOP
if errorlevel 5 goto ALL_BOTS
if errorlevel 4 goto BOT4
if errorlevel 3 goto BOT3
if errorlevel 2 goto BOT2
if errorlevel 1 goto BOT1

goto LOOP

:BOT1
cls
echo === GESTION BOT 1 - MULTI INDICATORS ===
echo 1. Redemarrer  2. Arreter  3. Demarrer  4. Voir logs  5. Retour
choice /c 12345 /m "Action"

if errorlevel 5 goto LOOP
if errorlevel 4 (
    notepad "%LOG_DIR1%\service.log"
    goto LOOP
)
if errorlevel 3 (
    net start "%SERVICE1%"
    pause
    goto LOOP
)
if errorlevel 2 (
    net stop "%SERVICE1%"
    pause
    goto LOOP
)
if errorlevel 1 (
    net stop "%SERVICE1%"
    timeout /t 3 >nul
    net start "%SERVICE1%"
    pause
    goto LOOP
)

goto LOOP

:BOT2
cls
echo === GESTION BOT 2 - NEWSPAPERS IPO ===
echo 1. Redemarrer  2. Arreter  3. Demarrer  4. Voir logs  5. Retour
choice /c 12345 /m "Action"

if errorlevel 5 goto LOOP
if errorlevel 4 (
    notepad "%LOG_DIR2%\service_ipo.log"
    goto LOOP
)
if errorlevel 3 (
    net start "%SERVICE2%"
    pause
    goto LOOP
)
if errorlevel 2 (
    net stop "%SERVICE2%"
    pause
    goto LOOP
)
if errorlevel 1 (
    net stop "%SERVICE2%"
    timeout /t 3 >nul
    net start "%SERVICE2%"
    pause
    goto LOOP
)

goto LOOP

:BOT3
cls
echo === GESTION BOT 3 - NEWSPAPERS BTC ===
echo 1. Redemarrer  2. Arreter  3. Demarrer  4. Voir logs  5. Retour
choice /c 12345 /m "Action"

if errorlevel 5 goto LOOP
if errorlevel 4 (
    notepad "%LOG_DIR3%\service_btc.log"
    goto LOOP
)
if errorlevel 3 (
    net start "%SERVICE3%"
    pause
    goto LOOP
)
if errorlevel 2 (
    net stop "%SERVICE3%"
    pause
    goto LOOP
)
if errorlevel 1 (
    net stop "%SERVICE3%"
    timeout /t 3 >nul
    net start "%SERVICE3%"
    pause
    goto LOOP
)

goto LOOP

:BOT4
cls
echo === GESTION BOT 4 - LTV_SYSTEM ===
echo 1. Redemarrer  2. Arreter  3. Demarrer  4. Dashboard  5. Retour
choice /c 12345 /m "Action"

if errorlevel 5 goto LOOP
if errorlevel 4 (
	notepad "%LOG_DIR4%\ltv_service.log"
    start "" "http://localhost:3000"
    goto LOOP
)
if errorlevel 3 (
    net start "CryptoBot_LTV_Alert"
    net start "CryptoBot_FastAPI"
    net start "CryptoBot_Frontend"
    pause
    goto LOOP
)
if errorlevel 2 (
    net stop CryptoBot_LTV_Alert
    net stop CryptoBot_FastAPI
    net stop CryptoBot_Frontend
    timeout /t 3 >nul
    if %errorlevel% neq 0 (
        echo [ERREUR] Echec de l'arret d'un service LTV_SYSTEM !
        sc query CryptoBot_LTV_Alert
        sc query CryptoBot_FastAPI
        sc query CryptoBot_Frontend
    )
    pause
    goto LOOP
)
goto LOOP

:ALL_BOTS
cls
echo === GESTION TOUS LES BOTS ===
echo 1. Redemarrer tous  2. Arreter tous  3. Demarrer tous  4. Retour
choice /c 1234 /m "Action"

if errorlevel 4 goto LOOP
if errorlevel 3 (
    net start "%SERVICE1%"
    net start "%SERVICE2%"
    net start "%SERVICE3%"
    net start "%SERVICE4%"
    pause
    goto LOOP
)
if errorlevel 2 (
    net stop "%SERVICE1%"
    net stop "%SERVICE2%"
    net stop "%SERVICE3%"
    net stop "%SERVICE4%"
    pause
    goto LOOP
)
if errorlevel 1 (
    net stop "%SERVICE1%"
    net stop "%SERVICE2%"
    net stop "%SERVICE3%"
    net stop "%SERVICE4%"
    timeout /t 3 >nul
    net start "%SERVICE1%"
    net start "%SERVICE2%"
    net start "%SERVICE3%"
    net start "%SERVICE4%"
    pause
    goto LOOP
)

goto LOOP
