@echo off
rem Starts the JARVIS voice server (port 3900) if it isn't already running, then the JARVIS app.
set "ROOT=%~dp0"
set "PYTHONPATH="
powershell -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 http://127.0.0.1:3900/health | Out-Null; exit 0 } catch { exit 1 }"
if errorlevel 1 (
  powershell -NoProfile -Command "Start-Process -WindowStyle Hidden -WorkingDirectory '%ROOT%voice_server' -FilePath '%ROOT%voice_server\.venv\Scripts\python.exe' -ArgumentList 'server.py' -RedirectStandardOutput '%ROOT%voice_server\voice.log' -RedirectStandardError '%ROOT%voice_server\voice.err.log'"
)
cd /d "%ROOT%app"
start "" "%ROOT%app\node_modules\electron\dist\electron.exe" .
