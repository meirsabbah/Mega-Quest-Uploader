@echo off
echo Installing dependencies...
pip install pyinstaller sv-ttk pillow cryptography

echo.
echo Detecting Python installation path...
for /f "tokens=*" %%i in ('python -c "import sys; print(sys.prefix)"') do set PYPREFIX=%%i
echo Python prefix: %PYPREFIX%

echo.
echo Closing any running instance...
taskkill /f /im "NitzFlash.exe" 2>nul
taskkill /f /im "Quest Mass Uploader.exe" 2>nul
timeout /t 1 /nobreak >nul

echo.
echo Cleaning previous build...
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build
if exist "NitzFlash.spec" del "NitzFlash.spec"

echo.
echo Building NitzFlash executable...
python -m PyInstaller --onefile --windowed ^
  --name "NitzFlash" ^
  --icon "icon.ico" ^
  --add-data "icon.ico;." ^
  --add-data "quest_uploader_logo.png;." ^
  --add-data "video_encrypter/logo.png;video_encrypter" ^
  --add-data "video_encrypter/logo_white.png;video_encrypter" ^
  --add-data "%PYPREFIX%\tcl\tcl8.6;tcl" ^
  --add-data "%PYPREFIX%\tcl\tk8.6;tk" ^
  --collect-all tkinter ^
  --collect-all sv_ttk ^
  --collect-all cryptography ^
  main.py

echo.
echo ===============================================
echo  Done! NitzFlash.exe is in the dist\ folder.
echo.
echo  Place these next to NitzFlash.exe before
echo  deploying to another machine:
echo    - ADB\          (Quest uploader)
echo    - client.exe    (Video encrypter player)
echo ===============================================
pause
