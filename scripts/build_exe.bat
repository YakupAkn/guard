@echo off
cd /d "%~dp0\.."
pip install pyinstaller -q
pyinstaller --noconfirm --onefile --windowed ^
  --name GuardSecurity ^
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
pause
