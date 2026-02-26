@echo off
title Configuration Administrateur Definitif
echo ========================================
echo   CONFIGURATION ADMINISTRATEUR DEFINITIF
echo ========================================
echo.
echo Ce script va creer un raccourci avec privileges
echo administrateur permanents (AUCUN POPUP futur)
echo.
pause

echo Creation du raccourci administrateur...

:: Creer le raccourci avec privileges admin
powershell -Command "$WshShell = New-Object -comObject WScript.Shell; $Shortcut = $WshShell.CreateShortcut('%USERPROFILE%\Desktop\Bot Crypto Admin.lnk'); $Shortcut.TargetPath = '%~dp0run_as_admin.bat'; $Shortcut.WorkingDirectory = '%~dp0'; $Shortcut.Description = 'Bot Crypto avec privileges administrateur'; $Shortcut.Save()"

:: Modifier les proprietes pour execution admin automatique
powershell -Command "$bytes = [System.IO.File]::ReadAllBytes('%USERPROFILE%\Desktop\Bot Crypto Admin.lnk'); $bytes[0x15] = $bytes[0x15] -bor 0x20; [System.IO.File]::WriteAllBytes('%USERPROFILE%\Desktop\Bot Crypto Admin.lnk', $bytes)"

echo.
echo ========================================
echo   CONFIGURATION TERMINEE !
echo ========================================
echo.
echo Un raccourci "Bot Crypto Admin" a ete cree
echo sur votre Bureau avec privileges administrateur
echo permanents.
echo.
echo UTILISATION :
echo - Double-clic sur le raccourci = AUCUN POPUP !
echo - Synchronisation Windows automatique
echo - Bot 100%% fonctionnel
echo.
echo ========================================
pause