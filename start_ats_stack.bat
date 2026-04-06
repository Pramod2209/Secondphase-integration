@echo off
setlocal

set "ROOT=%~dp0"
set "PY=C:/Users/JAYAN/anaconda3/envs/venv/python.exe"
set "API_URL=http://127.0.0.1:8000/api/health"
set "UI_URL=http://127.0.0.1:5173/"

echo [ATS] Root: %ROOT%

if not exist "%PY%" (
  echo [ATS] Python executable not found: %PY%
  exit /b 1
)

where npm >nul 2>nul
if errorlevel 1 (
  echo [ATS] npm is not available in PATH. Install Node.js and retry.
  exit /b 1
)

echo [ATS] Stopping existing ATS API/UI processes if any...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$procs = Get-CimInstance Win32_Process; foreach($p in $procs){ if($p.CommandLine -and ($p.CommandLine -like '*uvicorn ats.server:app*' -or $p.CommandLine -like '*vite --host 127.0.0.1 --port 5173*')){ try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop; Write-Output ('stopped_pid=' + $p.ProcessId) } catch {} } }"

echo [ATS] Starting backend API...
start "ATS API" cmd /k "cd /d %ROOT% && %PY% -m uvicorn ats.server:app --host 127.0.0.1 --port 8000"

echo [ATS] Waiting for backend health and warmup...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ok=$false; for($i=0; $i -lt 120; $i++){ try { $r = Invoke-RestMethod -Uri '%API_URL%' -Method Get -TimeoutSec 3; if($r.status -eq 'ok'){ $ok=$true; break } } catch {}; Start-Sleep -Seconds 1 }; if($ok){ Write-Output 'health_ok'; exit 0 } else { Write-Output 'health_failed'; exit 1 }"
if errorlevel 1 (
  echo [ATS] Backend did not become healthy in time.
  exit /b 1
)

echo [ATS] Starting React UI...
start "ATS UI" cmd /k "cd /d %ROOT%webui && npm run dev -- --host 127.0.0.1 --port 5173"

timeout /t 2 /nobreak >nul
start "" "%UI_URL%"

echo [ATS] Stack started.
echo [ATS] API: http://127.0.0.1:8000/api/health
echo [ATS] UI : %UI_URL%
exit /b 0
