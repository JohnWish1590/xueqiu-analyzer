@echo off
REM Xueqiu analyzer - one-click launcher (double-click to run)
REM Kills every lingering process on port 8765 (including zombie / half-dead ones),
REM then starts a fresh server and opens the browser in fullscreen.
REM Only touches port 8765; never affects other ports/services on this machine.

SETLOCAL

SET PYTHON=C:\Users\user\AppData\Local\Programs\Python\Python312\pythonw.exe
SET SRV=D:\SynologyDrive\CODING\xueqiu-analyzer\server.py
SET URL=http://localhost:8765
SET PORT=8765

echo [1/3] Cleaning up stale processes on port %PORT% ...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /C:":%PORT% " ^| findstr /C:"LISTENING"') do (
    echo    Killing PID %%a
    taskkill /PID %%a /F >nul 2>&1
)

timeout /t 1 /nobreak >nul

echo [2/3] Starting server (hidden, no console window)...
start "" "%PYTHON%" "%SRV%"

echo [3/3] Waiting for server to be ready...
SET /A tries=0
:wait
timeout /t 1 /nobreak >nul
SET /A tries+=1
netstat -ano 2>nul | findstr /C:":%PORT% " | findstr /C:"LISTENING" >nul
if not errorlevel 1 goto open
if %tries% GTR 30 (
    echo Failed to start server within 30 seconds. Check server.py for errors.
    pause
    exit /b 1
)
goto wait

:open
echo Server ready. Opening browser...
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter Name='msedge.exe'|Where-Object{$_.CommandLine -like '*http://localhost:8765*'}|ForEach-Object{$_.Terminate()};Start-Sleep -Milliseconds 400"
start "" msedge --app="%URL%" --start-maximized
exit
