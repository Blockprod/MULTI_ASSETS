$ErrorActionPreference = 'SilentlyContinue'
Write-Host "Fusion logs LTV en attente de nouvelles lignes..."
$files = @(
  @{Path='C:\Users\averr\BIBOT\LTV_SYSTEM\backend\ltv_service.log'; Prefix='[LTV Alert stdout] '},
  @{Path='C:\Users\averr\BIBOT\LTV_SYSTEM\backend\ltv_service_error.log'; Prefix='[LTV Alert stderr] '},
  @{Path='C:\Users\averr\BIBOT\LTV_SYSTEM\backend\fastapi_service.log'; Prefix='[FastAPI stdout] '},
  @{Path='C:\Users\averr\BIBOT\LTV_SYSTEM\backend\fastapi_service_error.log'; Prefix='[FastAPI stderr] '},
  @{Path='C:\Users\averr\BIBOT\LTV_SYSTEM\frontend\frontend_service.log'; Prefix='[Frontend stdout] '},
  @{Path='C:\Users\averr\BIBOT\LTV_SYSTEM\frontend\frontend_service_error.log'; Prefix='[Frontend stderr] '}
)
$positions = @{}
foreach ($f in $files) { if (Test-Path $f.Path) { $lines = Get-Content $f.Path; $positions[$f.Path] = $lines.Count } else { $positions[$f.Path] = 0 } }
while ($true) {
  $found = $false
  foreach ($f in $files) {
    if (Test-Path $f.Path) {
      $newLines = Get-Content $f.Path | Select-Object -Skip $positions[$f.Path]
      foreach ($line in $newLines) { Write-Host ($f.Prefix + $line); $found = $true }
      $positions[$f.Path] += $newLines.Count
    }
  }
  if (-not $found) { Start-Sleep -Milliseconds 500 }
}
$files = @(
  @{Path='C:\Users\averr\BIBOT\LTV_SYSTEM\backend\ltv_service.log'; Prefix='[LTV Alert stdout] '},
  @{Path='C:\Users\averr\BIBOT\LTV_SYSTEM\backend\ltv_service_error.log'; Prefix='[LTV Alert stderr] '},
  @{Path='C:\Users\averr\BIBOT\LTV_SYSTEM\backend\fastapi_service.log'; Prefix='[FastAPI stdout] '},
  @{Path='C:\Users\averr\BIBOT\LTV_SYSTEM\backend\fastapi_service_error.log'; Prefix='[FastAPI stderr] '},
  @{Path='C:\Users\averr\BIBOT\LTV_SYSTEM\frontend\frontend_service.log'; Prefix='[Frontend stdout] '},
  @{Path='C:\Users\averr\BIBOT\LTV_SYSTEM\frontend\frontend_service_error.log'; Prefix='[Frontend stderr] '}
)
$positions = @{}
foreach ($f in $files) { if (Test-Path $f.Path) { $lines = Get-Content $f.Path; $positions[$f.Path] = $lines.Count } else { $positions[$f.Path] = 0 } }
while ($true) {
  foreach ($f in $files) {
    if (Test-Path $f.Path) {
      $newLines = Get-Content $f.Path | Select-Object -Skip $positions[$f.Path]
      foreach ($line in $newLines) { Write-Host ($f.Prefix + $line) }
      $positions[$f.Path] += $newLines.Count
    }
  }
  Start-Sleep -Milliseconds 500
}