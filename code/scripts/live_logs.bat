@echo off
title Live Logs - Crypto Bots
echo Choisissez le bot a surveiller:
echo 1. Bot Multi Indicators
echo 2. Bot Newspapers IPO  
echo 3. Bot Newspapers BTC
echo 4. Bot LTV Monitor
echo 5. Tous les bots (split screen)
choice /c 12345 /m "Votre choix"

if errorlevel 5 goto ALL
if errorlevel 4 goto BOT4
if errorlevel 3 goto BOT3
if errorlevel 2 goto BOT2
if errorlevel 1 goto BOT1

 :BOT1
powershell "Get-Content 'C:\Users\averr\BIBOT\MULTI_ASSETS_BOT\code\src\service.log' -Wait -Tail 20"
goto END

:BOT2
powershell "Get-Content 'C:\Users\averr\NEWS_PAPERS\service_ipo.log' -Wait -Tail 20"
goto END

:BOT3
powershell "Get-Content 'C:\Users\averr\NEWS_PAPERS\service_btc.log' -Wait -Tail 20"
goto END


:BOT4
start powershell -NoExit -File "%~dp0ltv_logs_fusion.ps1"
goto END

 :ALL
start "Bot 1 Logs" powershell "Get-Content 'C:\Users\averr\BIBOT\MULTI_ASSETS_BOT\code\src\service.log' -Wait -Tail 15"
start "Bot 2 Logs" powershell "Get-Content 'C:\Users\averr\NEWS_PAPERS\service_ipo.log' -Wait -Tail 15"
start "Bot 3 Logs" powershell "Get-Content 'C:\Users\averr\NEWS_PAPERS\service_btc.log' -Wait -Tail 15"
start "LTV System (fusion)" powershell -NoExit -File "%~dp0ltv_logs_fusion.ps1"

:END