@echo off
cd /d "%~dp0\.."
pip install pyinstaller -q
pyinstaller --noconfirm --onefile --console ^
  --name GuardKurulum ^
  --icon assets/app.ico ^
  --version-file assets/version_kurulum.txt ^
  installer/kurulum.py
echo.
echo dist\GuardKurulum.exe
echo Imza icin: scripts\GuardImzala.ps1 -Exe dist\GuardKurulum.exe
pause
