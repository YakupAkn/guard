@echo off
cd /d "%~dp0\.."
pip install pyinstaller -q
pyinstaller --noconfirm --onefile --console --name GuardKurulum installer/kurulum.py
echo.
echo dist\GuardKurulum.exe  (sadece stdlib, ~8-12 MB beklenir)
pause
