@echo off
cd /d "%~dp0\.."
pip install pyinstaller -q
pyinstaller --noconfirm --onefile --windowed ^
  --name GuardSecurity ^
  --icon assets/app.ico ^
  --version-file assets/version.txt ^
  --add-data "assets/dashboard.html;." ^
  --paths src ^
  --hidden-import guard.ayarlar ^
  --hidden-import guard.kamera ^
  --hidden-import guard.yuz ^
  --hidden-import guard.yuz_kayit ^
  --hidden-import guard.kisayol ^
  src/guard/__main__.py
echo.
echo dist\GuardSecurity.exe
echo Imza icin: scripts\GuardImzala.ps1 -Exe dist\GuardSecurity.exe
pause
