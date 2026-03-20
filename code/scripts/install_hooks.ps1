# install_hooks.ps1 — MULTI_ASSETS
# Installe les git hooks depuis code/scripts/hooks/ vers .git/hooks/
# À exécuter après git clone ou mise à jour des hooks.
#
# Usage (depuis la racine du repo) :
#   .venv\Scripts\Activate.ps1
#   .\code\scripts\install_hooks.ps1

$repoRoot  = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$hooksDir  = Join-Path $repoRoot ".git\hooks"
$sourceDir = Join-Path $PSScriptRoot "hooks"

if (-not (Test-Path $hooksDir)) {
    Write-Error "Dossier .git/hooks introuvable. Êtes-vous dans un repo git ?"
    exit 1
}

$hooks = Get-ChildItem -Path $sourceDir -File

foreach ($hook in $hooks) {
    $dest = Join-Path $hooksDir $hook.Name
    Copy-Item -Path $hook.FullName -Destination $dest -Force
    # Sur Windows, git utilise bash (Git for Windows) — pas besoin de chmod
    # mais on peut forcer l'exécutabilité si bash est disponible
    $bash = Get-Command bash -ErrorAction SilentlyContinue
    if ($bash) {
        & bash -c "chmod +x '$($dest -replace '\\', '/')'"
    }
    Write-Host "✅ Hook installé : $($hook.Name) → .git/hooks/$($hook.Name)"
}

Write-Host "`nHooks installés. Actifs dès le prochain commit."
