@echo off
title Monitoring Serveur Trading - 24/7
color 0A

:LOOP
cls
echo ==========================================
echo     SERVEUR TRADING - MONITORING 24/7
echo ==========================================
echo.

echo [SYSTEME]
echo CPU: 
wmic cpu get loadpercentage /value | findstr "LoadPercentage"
echo RAM: 
wmic OS get TotalVisibleMemorySize,FreePhysicalMemory /value | findstr "="
echo Uptime: 
wmic os get lastbootuptime /value | findstr "LastBootUpTime"
echo.

echo [RESEAU]
echo Connexion Internet:
ping -n 1 8.8.8.8 >nul && echo [OK] Internet accessible || echo [ERREUR] Pas d'internet
ping -n 1 api.binance.com >nul && echo [OK] Binance accessible || echo [ERREUR] Binance inaccessible
echo.

echo [SERVICES TRADING]
sc query CryptoBot_MultiAssets | findstr "STATE"
sc query CryptoBot_NEWSPAPERS_IPO | findstr "STATE"
sc query CryptoBot_NEWSPAPERS_BTC | findstr "STATE"
echo.

echo [PROCESSUS PYTHON]
tasklist | findstr python.exe | find /c "python.exe" > temp.txt
set /p PYTHON_COUNT=<temp.txt
del temp.txt
echo Processus Python actifs: %PYTHON_COUNT%
echo.

echo [TEMPERATURE CPU]
wmic /namespace:\\root\wmi PATH MSAcpi_ThermalZoneTemperature get CurrentTemperature /value 2>nul | findstr "CurrentTemperature" || echo Temperature non disponible
echo.

echo Prochaine actualisation dans 30 secondes...
echo Appuyez sur une touche pour menu actions
timeout /t 30 /nobreak >nul
if errorlevel 1 goto MENU
goto LOOP

:MENU
cls
echo === MENU ACTIONS SERVEUR ===
echo 1. Redemarrer tous les services
echo 2. Optimiser les performances
echo 3. Nettoyer les logs
echo 4. Test connectivite complete
echo 5. Retour monitoring
choice /c 12345 /m "Action"

if errorlevel 5 goto LOOP
if errorlevel 4 goto TEST_NETWORK
if errorlevel 3 goto CLEAN_LOGS
if errorlevel 2 goto OPTIMIZE
if errorlevel 1 goto RESTART_SERVICES


:RESTART_SERVICES
echo Redemarrage de tous les services...
nssm restart CryptoBot_MultiAssets
nssm restart CryptoBot_NEWSPAPERS_IPO
nssm restart CryptoBot_NEWSPAPERS_BTC
pause
goto LOOP

:OPTIMIZE
echo Optimisation des performances...
wmic process where name="python.exe" CALL setpriority "high priority"
echo Nettoyage memoire...
echo. > "%temp%\empty.tmp"
del "%temp%\empty.tmp"
pause
goto LOOP


:CLEAN_LOGS
echo Nettoyage des anciens logs...
forfiles /p "C:\Users\averr\BIBOT\MULTI_ASSETS_BOT\code\src" /m "*.log" /d -7 /c "cmd /c del @path" 2>nul
forfiles /p "C:\Users\averr\NEWS_PAPERS" /m "*.log" /d -7 /c "cmd /c del @path" 2>nul
echo Logs nettoyes
pause
goto LOOP

:TEST_NETWORK
echo Test de connectivite complet...
echo Test Google DNS...
ping -n 4 8.8.8.8
echo Test Binance API...
ping -n 4 api.binance.com
echo Test serveur email...
ping -n 4 smtp.gmail.com
pause
goto LOOP