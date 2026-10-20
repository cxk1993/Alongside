@echo off
REM Alongside frpc as Windows service
cd /d "%~dp0"
"%~dp0frpc.exe" -c "%~dp0frpc.toml"
