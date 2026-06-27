# Starts an ngrok tunnel to the bot on port 8080, using the full ngrok path
# (works even when VS Code's terminal has a stale PATH).
#
# First time only, set your token:
#   .\tunnel.ps1 -Token YOUR_FULL_TOKEN
# After that, just:
#   .\tunnel.ps1
param([string]$Token = "")

$ngrok = "C:\Users\dell\AppData\Local\Microsoft\WinGet\Links\ngrok.exe"
if (-not (Test-Path $ngrok)) {
    $alt = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet" -Recurse -Filter ngrok.exe -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($alt) { $ngrok = $alt.FullName } else { Write-Error "ngrok.exe not found"; exit 1 }
}

if ($Token -ne "") {
    & $ngrok config add-authtoken $Token
    Write-Host "Token saved." -ForegroundColor Green
}

Write-Host "Starting tunnel to http://localhost:8080 ..." -ForegroundColor Cyan
Write-Host "Copy the https://....ngrok-free.app URL below and submit it." -ForegroundColor Yellow
& $ngrok http 8080
