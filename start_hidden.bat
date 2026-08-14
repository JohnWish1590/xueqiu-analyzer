@echo off
REM Xueqiu analyzer - silent launcher (run at login, no window)
REM Waits for D: (SynologyDrive) ready, then launches server via pythonw

SET PYTHON=C:\Users\user\AppData\Local\Programs\Python\Python312\pythonw.exe
SET SRV=D:\SynologyDrive\CODING\xueqiu-analyzer\server.py

SET /A tries=0
:wait
dir "%SRV%" >nul 2>&1
if not errorlevel 1 goto launch
SET /A tries+=1
if %tries% GTR 30 exit /b 1
timeout /t 5 /nobreak >nul
goto wait

:launch
start "" "%PYTHON%" "%SRV%"
exit
