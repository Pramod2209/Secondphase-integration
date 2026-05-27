@echo off
setlocal

set "ROOT=%~dp0"
set "API_URL=http://127.0.0.1:8000/api/health"
set "UI_URL=http://127.0.0.1:5173/"

echo [ATS-FAST] Root: %ROOT%

REM Activate virtual environment
if exist "%ROOT%.venv\Scripts\activate.bat" (
  call "%ROOT%.venv\Scripts\activate.bat"
) else (
  echo [ATS-FAST] Virtual environment not found at %ROOT%.venv
  exit /b 1
)

REM Ensure backend deps
python -c "import uvicorn" 2>nul
if errorlevel 1 (
  echo [ATS-FAST] Installing project requirements...
  python -m pip install -r ats\requirements.txt
  if errorlevel 1 (
    echo [ATS-FAST] Failed to install requirements
    exit /b 1
  )
)

REM Ensure npm exists
where npm >nul 2>nul
if errorlevel 1 (
  echo [ATS-FAST] npm is not available in PATH. Install Node.js and retry.
  exit /b 1
)

REM Fast mode runtime config (model-less bulk path)
set "ATS_USE_SEMANTIC_BULK=0"
set "ATS_USE_SEMANTIC_SINGLE=1"
set "ATS_WARMUP_EMBEDDER=0"
set "ATS_BATCH_SIZE=120"
set "ATS_MAX_CONCURRENT_BATCHES=4"
set "ATS_THREAD_WORKERS=24"
set "ATS_PROCESS_WORKERS=2"

echo [ATS-FAST] Stopping existing ATS API/UI processes if any...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$procs = Get-CimInstance Win32_Process; foreach($p in $procs){ if($p.CommandLine -and ($p.CommandLine -like '*uvicorn ats.server:app*' -or $p.CommandLine -like '*vite --host 127.0.0.1 --port 5173*')){ try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop; Write-Output ('stopped_pid=' + $p.ProcessId) } catch {} } }"

echo [ATS-FAST] Starting backend API...
start "ATS API FAST" cmd /k "cd /d %ROOT% && call .venv\Scripts\activate.bat && set ATS_USE_SEMANTIC_BULK=%ATS_USE_SEMANTIC_BULK% && set ATS_USE_SEMANTIC_SINGLE=%ATS_USE_SEMANTIC_SINGLE% && set ATS_WARMUP_EMBEDDER=%ATS_WARMUP_EMBEDDER% && set ATS_BATCH_SIZE=%ATS_BATCH_SIZE% && set ATS_MAX_CONCURRENT_BATCHES=%ATS_MAX_CONCURRENT_BATCHES% && set ATS_THREAD_WORKERS=%ATS_THREAD_WORKERS% && set ATS_PROCESS_WORKERS=%ATS_PROCESS_WORKERS% && python -m uvicorn ats.server:app --host 127.0.0.1 --port 8000 --log-level info"

echo [ATS-FAST] Waiting for backend health...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ok=$false; for($i=0; $i -lt 120; $i++){ try { $r = Invoke-RestMethod -Uri '%API_URL%' -Method Get -TimeoutSec 3; if($r.status -eq 'ok'){ $ok=$true; break } } catch {}; Start-Sleep -Seconds 1 }; if($ok){ Write-Output 'health_ok'; exit 0 } else { Write-Output 'health_failed'; exit 1 }"
if errorlevel 1 (
  echo [ATS-FAST] Backend did not become healthy in time.
  exit /b 1
)

echo [ATS-FAST] Starting React UI...
start "ATS UI" cmd /k "cd /d %ROOT%webui && npm run dev -- --host 127.0.0.1 --port 5173"

timeout /t 2 /nobreak >nul
start "" "%UI_URL%"

echo [ATS-FAST] Stack started.
echo [ATS-FAST] API: %API_URL%
echo [ATS-FAST] UI : %UI_URL%
echo [ATS-FAST] Mode: semantic bulk OFF (throughput optimized)
exit /b 0
