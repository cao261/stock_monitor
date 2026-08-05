@echo off
REM ============================================================
REM   A-Stock Sentiment Monitor v1.0  (one-click start)
REM
REM   - Free port %PORT% (kill any leftover uvicorn from last run)
REM   - Activate venv (if exists)
REM   - Install backend deps (first time)
REM   - Build frontend (if dist missing)
REM   - Launch PowerShell watcher that opens browser when ready
REM   - Run uvicorn in foreground (Ctrl+C to stop)
REM
REM   This file is intentionally English-only to avoid Windows
REM   cmd encoding issues (chcp 65001 vs GBK file parsing).
REM   All Chinese strings live in the Dashboard UI.
REM ============================================================
setlocal
cd /d %~dp0

set PORT=8000
set HOST=127.0.0.1

echo.
echo ============================================================
echo   A-Stock Sentiment Monitor v1.0
echo   Dashboard: http://%HOST%:%PORT%
echo   API docs : http://%HOST%:%PORT%/docs
echo ============================================================
echo.

REM ----- 0. Free port %PORT% (kill leftover uvicorn from last run) -----
echo [step] checking port %PORT% availability...
REM Use PowerShell to find the actual owning process of the port.
REM This is more reliable than findstr LISTENING on Chinese Windows.
powershell -NoProfile -Command "$p=%PORT%; $conn = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue; if ($conn) { $conn | ForEach-Object { try { $proc = Get-Process -Id $_.OwningProcess -ErrorAction Stop; Write-Host ('[step] killing leftover process on port %PORT% (PID ' + $_.OwningProcess + ': ' + $proc.ProcessName + ')'); Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue } catch { Write-Host ('[step] killing PID ' + $_.OwningProcess + ' (process already gone)'); Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue } } } else { Write-Host '[info] port %PORT% is free' }"
REM Give Windows TIME_WAIT sockets a moment to clear.
timeout /t 3 /nobreak >nul

REM ----- 1. Activate venv (optional) -----
if exist "venv\Scripts\activate.bat" (
  call "venv\Scripts\activate.bat"
) else (
  echo [info] no venv found, using system Python
)

REM ----- 2. Install backend deps (first time) -----
if not exist ".deps_installed" (
  echo [step] installing backend dependencies...
  python -m pip install --quiet -r requirements.txt
  if errorlevel 1 (
    echo [error] pip install failed
    pause
    exit /b 1
  )
  echo. > .deps_installed
) else (
  echo [info] backend deps already installed
)

REM ----- 3. Build frontend (if dist missing) -----
if not exist "frontend\dist\index.html" (
  echo [step] frontend dist not found, building...
  pushd frontend
  if not exist "node_modules" (
    echo [step] npm install ...
    call npm install
    if errorlevel 1 (
      popd
      echo [error] npm install failed
      pause
      exit /b 1
    )
  )
  call npm run build
  if errorlevel 1 (
    popd
    echo [error] npm run build failed
    pause
    exit /b 1
  )
  popd
) else (
  echo [info] frontend dist already built
)

REM ----- 4. Start background watcher that opens browser when server is ready -----
echo.
echo [step] starting browser launcher (waits for server)...
REM Use a separate .ps1 file (much more reliable than inline -Command in cmd).
REM The .ps1 writes to _browser_launcher.log so any failure is visible.
if exist "%~dp0_open_browser.ps1" (
  start "browser-launcher" /min powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0_open_browser.ps1"
) else (
  echo [warn] _open_browser.ps1 not found, will not auto-open browser
  echo        you can open http://%HOST%:%PORT%/ manually
)

REM ----- 5. Run uvicorn in foreground -----
echo.
echo [step] launching uvicorn on http://%HOST%:%PORT%
echo        press Ctrl+C to stop
echo.

uvicorn app.main:app --host %HOST% --port %PORT%

REM ----- 6. Server stopped -----
echo.
echo [info] server stopped
pause
