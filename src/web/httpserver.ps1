if (-not (Get-Command http-server -ErrorAction SilentlyContinue)) {
    Write-Error "Error: http-server is not installed or not in PATH"
    exit 1
}

cd "$PSScriptRoot/static"; http-server
