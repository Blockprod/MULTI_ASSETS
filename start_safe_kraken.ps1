param(
    [ValidateSet("hidden", "console")]
    [string]$Mode = "hidden",
    [switch]$RestartIfRunning
)

# Wrapper anti-doublon pour MULTI_ASSETS Kraken.
$scriptPath       = "C:\Users\averr\MULTI_ASSETS\code\src\KRAKEN_SYMBOLS.py"
$pythonHiddenExe  = "C:\Users\averr\MULTI_ASSETS\.venv\Scripts\pythonw.exe"
$pythonConsoleExe = "C:\Users\averr\MULTI_ASSETS\.venv\Scripts\python.exe"
$lockFile         = "C:\Users\averr\MULTI_ASSETS\.running_kraken.lock"
$heartbeatFile    = "C:\Users\averr\MULTI_ASSETS\code\src\kraken_bot\states\heartbeat_kraken.json"
$workingDirectory = "C:\Users\averr\MULTI_ASSETS\code\src"

$utf8NoBom = New-Object System.Text.UTF8Encoding $false
[Console]::OutputEncoding = $utf8NoBom
[Console]::InputEncoding = $utf8NoBom
$OutputEncoding = $utf8NoBom
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

function Get-KrakenBotProcesses {
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -match [regex]::Escape($scriptPath) }
}

function Get-HeartbeatPid {
    if (!(Test-Path $heartbeatFile)) {
        return $null
    }
    try {
        $heartbeat = Get-Content $heartbeatFile -Raw -ErrorAction Stop | ConvertFrom-Json
        if ($heartbeat -and $heartbeat.pid) {
            return [int]$heartbeat.pid
        }
    }
    catch {
    }
    return $null
}

function Resolve-ActiveKrakenPid {
    param([int]$LauncherPid)

    $deadline = (Get-Date).AddSeconds(10)
    do {
        $bots = @(Get-KrakenBotProcesses)
        if ($bots.Count -gt 0) {
            $botPidList = @($bots | Select-Object -ExpandProperty ProcessId | Sort-Object -Unique)
            $heartbeatPid = Get-HeartbeatPid
            if ($heartbeatPid -and ($botPidList -contains $heartbeatPid)) {
                return $heartbeatPid
            }

            $child = $bots |
                Where-Object { $_.ParentProcessId -eq $LauncherPid -and $_.ProcessId -ne $LauncherPid } |
                Select-Object -First 1
            if ($child) {
                return [int]$child.ProcessId
            }

            $nonLauncher = $bots |
                Where-Object { $_.ProcessId -ne $LauncherPid } |
                Select-Object -First 1
            if ($nonLauncher) {
                return [int]$nonLauncher.ProcessId
            }

            if ($botPidList -contains $LauncherPid) {
                return $LauncherPid
            }
        }
        Start-Sleep -Milliseconds 250
    } while ((Get-Date) -lt $deadline)

    return $LauncherPid
}

if (!(Test-Path $scriptPath)) {
    Write-Host "[start_safe_kraken] Script introuvable: $scriptPath"
    exit 1
}
if (!(Test-Path $pythonHiddenExe)) {
    Write-Host "[start_safe_kraken] Python introuvable: $pythonHiddenExe"
    exit 1
}
if (!(Test-Path $pythonConsoleExe)) {
    Write-Host "[start_safe_kraken] Python introuvable: $pythonConsoleExe"
    exit 1
}

$runningBots = @(Get-KrakenBotProcesses)

if ($runningBots -and $runningBots.Count -gt 0) {
    $pids = ($runningBots | Select-Object -ExpandProperty ProcessId | Sort-Object -Unique) -join ","
    if ($Mode -eq "console" -and $RestartIfRunning) {
        Write-Host "[start_safe_kraken] Bot Kraken deja lance (PID=$pids) -> redemarrage demande."
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
                $heartbeatPid = Get-HeartbeatPid
                if ($heartbeatPid -and ($runningPidList -contains $heartbeatPid)) {
                    $activePid = $heartbeatPid
                }
            }
            catch {
            }
        }
        if (-not $activePid) {
            $launcherCandidate = $runningPidList | Select-Object -First 1
            $activePid = Resolve-ActiveKrakenPid -LauncherPid $launcherCandidate
        }
        if ($activePid) {
            $activePid | Set-Content $lockFile
        }
        Write-Host "[start_safe_kraken] Bot Kraken deja lance (PID=$pids)."
        exit 0
    }
}

if (Test-Path $lockFile) {
    $pidStored = (Get-Content $lockFile -ErrorAction SilentlyContinue | Select-Object -First 1).Trim()
    if ($pidStored -match '^\d+$') {
        $proc = Get-Process -Id ([int]$pidStored) -ErrorAction SilentlyContinue
        if ($proc -and $proc.Name -like "*python*") {
            Write-Host "[start_safe_kraken] Bot Kraken deja lance (PID=$pidStored)."
            exit 0
        }
        Write-Host "[start_safe_kraken] Lock stale (PID mort=$pidStored) -> nettoyage."
        Remove-Item $lockFile -Force -ErrorAction SilentlyContinue
    }
    else {
        Write-Host "[start_safe_kraken] Lock invalide ('$pidStored') -> nettoyage."
        Remove-Item $lockFile -Force -ErrorAction SilentlyContinue
    }
}

"STARTING" | Set-Content $lockFile
if ($Mode -eq "console") {
    $p = Start-Process -FilePath $pythonConsoleExe -ArgumentList "-B $scriptPath" -WorkingDirectory $workingDirectory -NoNewWindow -PassThru
    $activePid = Resolve-ActiveKrakenPid -LauncherPid $p.Id
    $activePid | Set-Content $lockFile
    if ($activePid -ne $p.Id) {
        Write-Host "[start_safe_kraken] Bot Kraken demarre en mode console (PID=$activePid, launcher=$($p.Id))."
    }
    else {
        Write-Host "[start_safe_kraken] Bot Kraken demarre en mode console (PID=$($p.Id))."
    }
    Wait-Process -Id $activePid
    if (Test-Path $lockFile) {
        $stored = (Get-Content $lockFile -ErrorAction SilentlyContinue | Select-Object -First 1).Trim()
        if ($stored -eq [string]$activePid) {
            Remove-Item $lockFile -Force -ErrorAction SilentlyContinue
        }
    }
}
else {
    $p = Start-Process -FilePath $pythonHiddenExe -ArgumentList "-B `"$scriptPath`"" -WindowStyle Hidden -WorkingDirectory $workingDirectory -PassThru
    $activePid = Resolve-ActiveKrakenPid -LauncherPid $p.Id
    $activePid | Set-Content $lockFile
    if ($activePid -ne $p.Id) {
        Write-Host "[start_safe_kraken] Bot Kraken demarre en arriere-plan (PID=$activePid, launcher=$($p.Id))."
    }
    else {
        Write-Host "[start_safe_kraken] Bot Kraken demarre en arriere-plan (PID=$($p.Id))."
    }
}
