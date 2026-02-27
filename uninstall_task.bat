@echo off
chcp 65001 >nul 2>&1
title MULTI_ASSETS - Désinstallation Tâche Planifiée
echo.

:: ── Vérifier les droits admin ──
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] Droits administrateur requis. Relance en tant qu'admin...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

echo [*] Arrêt de la tâche MULTI_ASSETS...
schtasks /end /tn "MULTI_ASSETS" >nul 2>&1

echo [*] Suppression de la tâche MULTI_ASSETS...
schtasks /delete /tn "MULTI_ASSETS" /f >nul 2>&1

if %errorlevel% equ 0 (
    echo [OK] Tâche MULTI_ASSETS supprimée avec succès.
) else (
    echo [INFO] La tâche MULTI_ASSETS n'existait pas.
)

echo.
pause
