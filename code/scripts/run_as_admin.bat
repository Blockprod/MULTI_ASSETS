@echo off
title Bot Crypto - Mode Administrateur
echo ========================================
echo   BOT CRYPTO - PRIVILEGES ADMINISTRATEUR
echo ========================================
echo.
echo Synchronisation Windows automatique : ACTIVE
echo Privileges administrateur : ACTIVE
echo Aucun popup pendant l'execution
echo.
echo Demarrage du bot...
echo.

cd /d "C:\Users\averr\BIBOT\MULTI_INDICATORS_USDC"
call .venv\Scripts\activate.bat
python MULTI_SYMBOLS.py

echo.
echo Bot arrete. Appuyez sur une touche pour fermer...
pause >nul