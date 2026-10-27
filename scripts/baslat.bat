@echo off
cd /d "%~dp0\.."
python -m guard
if errorlevel 1 pause
