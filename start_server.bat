@echo off
REM Xueqiu analyzer - local web server launcher (double-click to run)
title XueqiuAnalyzer

SET PYTHON=C:\Users\user\.workbuddy\binaries\python\versions\3.13.12\python.exe
SET DIR=D:\SynologyDrive\CODING\xueqiu-analyzer

cd /d "%DIR%"
echo ============================================
echo  Xueqiu analyzer local server
echo  Starting web server...
echo  Open browser: http://localhost:8765
echo  Close this window to stop the server
echo ============================================
"%PYTHON%" server.py
pause
