@echo off
rem AR-Inspector for Windows:  launch.bat [setup|run|check] [options]
rem Double-click starts the stand; all logic lives in start.py.
cd /d "%~dp0"
set "PYCMD="
where py >nul 2>nul && set "PYCMD=py -3"
if not defined PYCMD where python >nul 2>nul && set "PYCMD=python"
if not defined PYCMD (
  echo Python 3.10+ not found. Install it from https://www.python.org/downloads/ and run again.
  pause
  exit /b 1
)
%PYCMD% start.py %*
if errorlevel 1 pause
