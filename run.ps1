# Run the Vera bot (Windows PowerShell).
# Loads .env if present, then starts uvicorn on port 8080.
if (Test-Path ".env") {
    Get-Content ".env" | ForEach-Object {
        if ($_ -match '^\s*([^#=]+)=(.*)$') {
            [Environment]::SetEnvironmentVariable($matches[1].Trim(), $matches[2].Trim())
        }
    }
}
uvicorn bot:app --host 0.0.0.0 --port 8080
