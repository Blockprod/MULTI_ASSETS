# Script de lancement du bot trading crypto (PowerShell)
# Désactive TOUS les caches Python pour éviter les problèmes de bytecode obsolète

Write-Host ""
Write-Host "════════════════════════════════════════════════════════════════"
Write-Host "  BOT DE TRADING CRYPTO - Lancement avec Anti-Cache"
Write-Host "════════════════════════════════════════════════════════════════"
Write-Host ""

$BotDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# Étape 1: Nettoyer TOUS les caches Python
Write-Host "[1/3] Nettoyage des caches Python..."
$pycacheDirs = Get-ChildItem -Path $BotDir -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue

if ($pycacheDirs.Count -gt 0) {
    foreach ($dir in $pycacheDirs) {
        Write-Host "   Suppression: $($dir.FullName)"
        Remove-Item -Path $dir.FullName -Recurse -Force -ErrorAction SilentlyContinue
    }
} else {
    Write-Host "   Aucun cache trouvé."
}
Write-Host "   Cache nettoyé."
Write-Host ""

# Étape 2: Nettoyer les .pyc individuels
Write-Host "[2/3] Recherche de fichiers .pyc obsolètes..."
$pycFiles = Get-ChildItem -Path $BotDir -Recurse -Filter "*.pyc" -ErrorAction SilentlyContinue

if ($pycFiles.Count -gt 0) {
    foreach ($file in $pycFiles) {
        Write-Host "   Suppression: $($file.Name)"
        Remove-Item -Path $file.FullName -Force -ErrorAction SilentlyContinue
    }
} else {
    Write-Host "   Aucun fichier .pyc trouvé."
}
Write-Host "   Fichiers .pyc nettoyés."
Write-Host ""

# Étape 3: Lancer le bot avec anti-cache activé
Write-Host "[3/3] Lancement du bot..."
Write-Host ""

Set-Location -Path $BotDir

# Variables d'environnement anti-cache
$env:PYTHONDONTWRITEBYTECODE = "1"
$env:PYTHONUNBUFFERED = "1"

# Lancer Python avec le flag -B (pas de fichiers .pyc)
& python -B MULTI_SYMBOLS.py

# Gestion des erreurs
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "════════════════════════════════════════════════════════════════"
    Write-Host "  ERREUR: Le bot s'est arrêté de manière inattendue"
    Write-Host "  Code d'erreur: $LASTEXITCODE"
    Write-Host "════════════════════════════════════════════════════════════════"
    Write-Host ""
    Read-Host "Appuyez sur Entrée pour fermer"
}
