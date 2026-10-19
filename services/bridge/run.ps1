# Alongside Note Bridge launcher (Windows, runs as nssm service)
# Listens on 127.0.0.1 only; frpc exposes it to the cloud server.
# Pure ASCII + CRLF. Edit paths below to match this machine.
$ErrorActionPreference = "Stop"

$ProjectRoot = "E:\ai work\work\life"
$BridgeDir   = Join-Path $ProjectRoot "services\bridge"
$VenvPython  = Join-Path $ProjectRoot "services\api\.venv\Scripts\python.exe"
$ConfigPath  = Join-Path $BridgeDir "config.yaml"

# PORT and PSK come from the nssm service environment (set once):
#   nssm set lifeos-bridge AppEnvironmentExtra BRIDGE_PORT=8790 BRIDGE_PSK=**** BRIDGE_CONFIG=...
$BridgePort = $env:BRIDGE_PORT
if (-not $BridgePort) { $BridgePort = "8790" }

$env:BRIDGE_CONFIG = $ConfigPath
if (-not $env:BRIDGE_PSK) {
    Write-Warning "BRIDGE_PSK is not set; the bridge refuses all requests until provided via environment."
}

# Insert services/ onto sys.path so 'bridge' is importable as a package,
# then build the app and serve it on the loopback interface only.
& $VenvPython -c "import sys; sys.path.insert(0, r'$BridgeDir\..'); from bridge.main import create_bridge_app; import uvicorn; uvicorn.run(create_bridge_app(), host='127.0.0.1', port=int('$BridgePort'), reload=False)"
