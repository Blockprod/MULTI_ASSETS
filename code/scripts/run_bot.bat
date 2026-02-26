@echo off
REM Script de lancement du bot trading crypto
REM Désactive TOUS les caches Python pour éviter les problèmes de bytecode obsolète

SETLOCAL ENABLEDELAYEDEXPANSION

REM Obtenir le répertoire courant
set "BOT_DIR=%~dp0"

echo.
echo ════════════════════════════════════════════════════════════════
echo   BOT DE TRADING CRYPTO - Lancement avec Anti-Cache
echo ════════════════════════════════════════════════════════════════
echo.

REM Étape 1: Nettoyer TOUS les caches Python
echo [1/3] Nettoyage des caches Python...
for /d /r "%BOT_DIR%" %%i in (__pycache__) do (
    if exist "%%i" (
        echo   Suppression: %%i
        rmdir /s /q "%%i" 2>nul
    )
)

echo   Cache nettoyé.
echo.

REM Étape 2: Nettoyer les .pyc individuels si présents
echo [2/3] Recherche de fichiers .pyc obsolètes...
for /r "%BOT_DIR%" %%i in (*.pyc) do (
    if exist "%%i" (
        echo   Suppression: %%~nxi
        del "%%i" 2>nul
    )
)

echo   Fichiers .pyc nettoyés.
echo.

REM Étape 3: Lancer le bot avec anti-cache activé
echo [3/3] Lancement du bot...
echo.

cd /d "%BOT_DIR%"

REM Variables d'environnement anti-cache
set PYTHONDONTWRITEBYTECODE=1
set PYTHONUNBUFFERED=1

REM Lancer Python avec le flag -B (pas de fichiers .pyc)
python -B MULTI_SYMBOLS.py

REM Si crash, afficher message d'erreur
if errorlevel 1 (
    echo.
    echo ════════════════════════════════════════════════════════════════
    echo   ERREUR: Le bot s'est arrêté de manière inattendue
    echo ════════════════════════════════════════════════════════════════
    echo.
    pause
)

endlocal
