@echo off
REM Xueqiu analyzer - one-click launcher (double-click to run)
REM Starts server in background (no console window), waits for port 8765,
REM then opens Microsoft Edge in fullscreen. WorkBuddy not required.

SETLOCAL

SET PYTHON=C:\Users\user\AppData\Local\Programs\Python\Python312\pythonw.exe
SET SRV=D:\SynologyDrive\CODING\xueqiu-analyzer\server.py
SET URL=http://localhost:8765

:: 1) Check if server is already running
netstat -ano 2>nul | findstr ":8765" | findstr "LISTENING" >nul
if not errorlevel 1 goto open

:: 2) Start server hidden (pythonw = no console window, detached from this batch)
echo Starting xueqiu-analyzer server...
start "" "%PYTHON%" "%SRV%"

:: 3) Wait up to 30 seconds for port 8765
SET /A tries=0
:wait
if %tries% GTR 30 (
  echo Failed to start server within 30 seconds.
  pause
  exit /b 1
)
timeout /t 1 /nobreak >nul
SET /A tries+=1
netstat -ano 2>nul | findstr ":8765" | findstr "LISTENING" >nul
if errorlevel 1 goto wait

:open
:: 4) Open Edge in fullscreen and exit this launcher
echo Server ready. Opening browser...
start msedge --start-fullscreen "%URL%"
exit