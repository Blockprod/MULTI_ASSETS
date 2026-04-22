param(
    [ValidateSet("hidden", "console")]
    [string]$Mode = "hidden",
    [switch]$RestartIfRunning
)

# Wrapper anti-doublon pour MULTI_ASSETS (lock file)
$scriptPath       = "C:\Users\averr\MULTI_ASSETS\code\src\MULTI_SYMBOLS.py"
$pythonHiddenExe  = "C:\Users\averr\MULTI_ASSETS\.venv\Scripts\pythonw.exe"
$pythonConsoleExe = "C:\Users\averr\MULTI_ASSETS\.venv\Scripts\python.exe"
$lockFile         = "C:\Users\averr\MULTI_ASSETS\.running.lock"
$heartbeatFile    = "C:\Users\averr\MULTI_ASSETS\code\src\states\heartbeat.json"

if (!(Test-Path $scriptPath)) {
    Write-Host "[start_safe] Script introuvable: $scriptPath"
    exit 1
}
if (!(Test-Path $pythonHiddenExe)) {
    Write-Host "[start_safe] Python introuvable: $pythonHiddenExe"
    exit 1
}
if (!(Test-Path $pythonConsoleExe)) {
    Write-Host "[start_safe] Python introuvable: $pythonConsoleExe"
    exit 1
}

$runningBots = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -match [regex]::Escape($scriptPath) }

if ($runningBots -and $runningBots.Count -gt 0) {
    $pids = ($runningBots | Select-Object -ExpandProperty ProcessId | Sort-Object -Unique) -join ","
    if ($Mode -eq "console" -and $RestartIfRunning) {
        Write-Host "[start_safe] Bot deja lance (PID=$pids) -> redemarrage console demande."
        foreach ($botPid in ($runningBots | Select-Object -ExpandProperty ProcessId | Sort-Object -Unique)) {
            Stop-Process -Id $botPid -Force -ErrorAction SilentlyContinue
        }
        Start-Sleep -Milliseconds 600
        if (Test-Path $lockFile) {
            Remove-Item $lockFile -Force -ErrorAction SilentlyContinue
        }
    }
    else {
        $activePid = $null
        $runningPidList = @($runningBots | Select-Object -ExpandProperty ProcessId | Sort-Object -Unique)
        if (Test-Path $heartbeatFile) {
            try {
                $heartbeat = Get-Content $heartbeatFile -Raw -ErrorAction Stop | ConvertFrom-Json
                $heartbeatPid = 0
                if ($heartbeat -and $heartbeat.pid) {
                    $heartbeatPid = [int]$heartbeat.pid
                }
                if ($heartbeatPid -and ($runningPidList -contains $heartbeatPid)) {
                    $activePid = $heartbeatPid
                }
            }
            catch {
            }
        }
        if (-not $activePid) {
            $activePid = $runningPidList | Select-Object -First 1
        }
        if ($activePid) {
            $activePid | Set-Content $lockFile
        }
        Write-Host "[start_safe] Bot deja lance (PID=$pids)."
        if ($Mode -eq "console") {
            Write-Host "[start_safe] Pour voir Rich en direct: option 3 puis option 7."
        }
        exit 0
    }
}

if (Test-Path $lockFile) {
    $pidStored = (Get-Content $lockFile -ErrorAction SilentlyContinue | Select-Object -First 1).Trim()
    if ($pidStored -match '^\d+$') {
        $proc = Get-Process -Id ([int]$pidStored) -ErrorAction SilentlyContinue
        if ($proc -and $proc.Name -like "*python*") {
            Write-Host "[start_safe] Bot deja lance (PID=$pidStored)."
            exit 0
        }
        Write-Host "[start_safe] Lock stale (PID mort=$pidStored) -> nettoyage."
        Remove-Item $lockFile -Force -ErrorAction SilentlyContinue
    }
    else {
        Write-Host "[start_safe] Lock invalide ('$pidStored') -> nettoyage."
        Remove-Item $lockFile -Force -ErrorAction SilentlyContinue
    }
}

"STARTING" | Set-Content $lockFile
if ($Mode -eq "console") {
    $p = Start-Process -FilePath $pythonConsoleExe -ArgumentList "-B $scriptPath" -WorkingDirectory "C:\Users\averr\MULTI_ASSETS\code\src" -NoNewWindow -PassThru
    $p.Id | Set-Content $lockFile
    Write-Host "[start_safe] Bot demarre en mode console (PID=$($p.Id))."
    Wait-Process -Id $p.Id
    if (Test-Path $lockFile) {
        $stored = (Get-Content $lockFile -ErrorAction SilentlyContinue | Select-Object -First 1).Trim()
        if ($stored -eq [string]$p.Id) {
            Remove-Item $lockFile -Force -ErrorAction SilentlyContinue
        }
    }
}
else {
    $p = Start-Process -FilePath $pythonHiddenExe -ArgumentList "-B $scriptPath" -WindowStyle Hidden -WorkingDirectory "C:\Users\averr\MULTI_ASSETS\code\src" -PassThru
    $p.Id | Set-Content $lockFile
    Write-Host "[start_safe] Bot demarre (PID=$($p.Id))."
}
