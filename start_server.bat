@echo off
REM Xueqiu analyzer - local web server launcher (double-click to run)
REM Kills stale processes on port 8765, starts the server in a MINIMIZED
REM console (logs still readable if you restore it), waits until ready,
REM then opens the page in a single Edge "app" window (maximized, taskbar
REM visible, no tab bar) after closing any previous instance of it.

SETLOCAL

SET PYTHON=C:\Users\user\AppData\Local\Programs\Python\Python312\python.exe
SET SRV=D:\SynologyDrive\CODING\xueqiu-analyzer\server.py
SET URL=http://localhost:8765
SET PORT=8765

REM --- Minimize this launcher window so it never steals focus ---
title XQ_LAUNCHER_8765
powershell -NoProfile -Command "$h=(Get-Process cmd|Where-Object{$_.MainWindowTitle -eq 'XQ_LAUNCHER_8765'}|Select-Object -First 1).MainWindowHandle;Add-Type -TypeDefinition 'using System;using System.Runtime.InteropServices;public class W{[DllImport("user32.dll")]public static extern bool ShowWindow(IntPtr h,int n);}';[W]::ShowWindow($h,2)" >nul 2>&1

echo ============================================
echo  Xueqiu analyzer local server
echo ============================================

echo [1/3] Cleaning up stale processes on port %PORT% ...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /C:":%PORT% " ^| findstr /C:"LISTENING"') do (
    echo    Killing PID %%a
    taskkill /PID %%a /F >nul 2>&1
)

timeout /t 1 /nobreak >nul

echo [2/3] Starting server (minimized console for logs)...
start /MIN "" "%PYTHON%" "%SRV%"

echo [3/3] Waiting for server, then opening browser...
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
REM Close any previous app window for this tool, then open a fresh one.
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter Name='msedge.exe'|Where-Object{$_.CommandLine -like '*http://localhost:8765*'}|ForEach-Object{$_.Terminate()};Start-Sleep -Milliseconds 400"
start "" msedge --app="%URL%" --start-maximized
echo Server is running. Use the shutdown button in the page to stop it.
timeout /t 2 /nobreak >nul
exit
