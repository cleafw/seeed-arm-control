$ErrorActionPreference = "Continue"

function Get-ServiceListeners {
    Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
        Where-Object { $_.LocalPort -in 8000, 5173 }
}

# Ask the backend to stop its controller loop first. This lets FastAPI run
# hardware cleanup and close CH343 serial handles before the process exits.
try {
    Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/shutdown" -TimeoutSec 2 | Out-Null
    Write-Host "Backend shutdown requested; waiting for serial cleanup..."
} catch {
    # No backend, an older backend, or an unresponsive backend: fall through.
}

$deadline = [DateTime]::UtcNow.AddSeconds(6)
while ((Get-ServiceListeners) -and [DateTime]::UtcNow -lt $deadline) {
    Start-Sleep -Milliseconds 250
}

# Cover services launched from cmd.exe, PowerShell, uv.exe, an IDE, or this
# repository's batch file. Build needles at runtime so this script does not
# match its own PowerShell command line.
$backendNeedle = "uvicorn" + " backend.app:app"
$frontendNeedle = "npm" + " run dev"
$viteNeedle = "vite" + " --host 127.0.0.1 --port 5173"
$targets = Get-CimInstance Win32_Process | Where-Object {
    $_.ProcessId -ne $PID -and
    $_.CommandLine -and
    ($_.CommandLine.Contains($backendNeedle) -or
     $_.CommandLine.Contains($frontendNeedle) -or
     $_.CommandLine.Contains($viteNeedle))
}

foreach ($target in $targets) {
    Write-Host "Stopping residual process tree PID $($target.ProcessId) ($($target.Name))"
    & taskkill.exe /PID $target.ProcessId /T /F 2>$null | Out-Null
}

Start-Sleep -Milliseconds 500
foreach ($listener in (Get-ServiceListeners)) {
    Write-Host "Stopping listener PID $($listener.OwningProcess) on port $($listener.LocalPort)"
    & taskkill.exe /PID $listener.OwningProcess /T /F 2>$null | Out-Null
}

Start-Sleep -Seconds 1
$remaining = Get-ServiceListeners
if ($remaining) {
    $summary = ($remaining | ForEach-Object { "$($_.LocalPort)/PID $($_.OwningProcess)" }) -join ", "
    Write-Host "[ERROR] Listeners still active: $summary"
    exit 1
}

Write-Host "Backend and frontend listeners are stopped."
exit 0
