# Vera bot watchdog: keeps the bot (port 8080) and the ngrok tunnel (permanent
# domain) alive forever. Restarts either within ~30s if it dies. Registered as a
# Scheduled Task that runs at logon, so it survives reboots too.
$ErrorActionPreference = "SilentlyContinue"

$botDir = "G:\VERA\bot"
$domain = "https://rockfish-illicitly-chapter.ngrok-free.dev"
$ngrok  = "C:\Users\dell\AppData\Local\Microsoft\WinGet\Links\ngrok.exe"
$log    = Join-Path $botDir "keepalive.log"

function Log($m) { "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $m" | Out-File -FilePath $log -Append -Encoding utf8 }

function Test-Bot {
    try { return (Invoke-WebRequest "http://127.0.0.1:8080/v1/healthz" -UseBasicParsing -TimeoutSec 5).StatusCode -eq 200 }
    catch { return $false }
}
function Test-Tunnel {
    try {
        $t = (Invoke-WebRequest "http://127.0.0.1:4040/api/tunnels" -UseBasicParsing -TimeoutSec 5).Content | ConvertFrom-Json
        return ($t.tunnels.public_url -contains $domain)
    } catch { return $false }
}

Log "watchdog started"
while ($true) {
    if (-not (Test-Bot)) {
        Log "bot down -> starting"
        Start-Process -FilePath "python" -ArgumentList "-m","uvicorn","bot:app","--host","0.0.0.0","--port","8080" `
            -WorkingDirectory $botDir -WindowStyle Hidden
        Start-Sleep 6
    }
    if (-not (Test-Tunnel)) {
        Log "tunnel down -> restarting"
        Get-Process ngrok -ErrorAction SilentlyContinue | Stop-Process -Force
        Start-Sleep 2
        Start-Process -FilePath $ngrok -ArgumentList "http","8080","--url=$domain","--log","stdout" -WindowStyle Hidden
        Start-Sleep 6
    }
    Start-Sleep 30
}
