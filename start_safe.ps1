# Wrapper anti-doublon pour MULTI_ASSETS (lock file)
$scriptPath = "C:\Users\averr\MULTI_ASSETS\code\src\MULTI_SYMBOLS.py"
$pythonExe  = "C:\Users\averr\MULTI_ASSETS\.venv\Scripts\pythonw.exe"
$lockFile   = "C:\Users\averr\MULTI_ASSETS\.running.lock"

if (Test-Path $lockFile) {
    $pid_stored = (Get-Content $lockFile -ErrorAction SilentlyContinue).Trim()
    if ($pid_stored -notmatch '^\d+$') { exit 0 }
    $proc = Get-Process -Id ([int]$pid_stored) -ErrorAction SilentlyContinue
    if ($proc -and $proc.Name -like "*python*") { exit 0 }
    Remove-Item $lockFile -Force
}

"STARTING" | Set-Content $lockFile
$p = Start-Process -FilePath $pythonExe -ArgumentList "-B $scriptPath" -WindowStyle Hidden -WorkingDirectory "C:\Users\averr\MULTI_ASSETS\code\src" -PassThru
$p.Id | Set-Content $lockFile
