@echo off
echo Installing PyInstaller...
pip install pyinstaller

echo.
echo Detecting Python installation path...
for /f "tokens=*" %%i in ('python -c "import sys; print(sys.prefix)"') do set PYPREFIX=%%i
echo Python prefix: %PYPREFIX%

echo.
echo Closing any running instance...
taskkill /f /im "Quest Mass Uploader.exe" 2>nul
timeout /t 1 /nobreak >nul

echo.
echo Cleaning previous build...
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build
if exist "Quest Mass Uploader.spec" del "Quest Mass Uploader.spec"

echo.
echo Building executable...
python -m PyInstaller --onefile --windowed ^
  --name "Quest Mass Uploader" ^
  --add-data "%PYPREFIX%\tcl\tcl8.6;tcl" ^
  --add-data "%PYPREFIX%\tcl\tk8.6;tk" ^
  --collect-all tkinter ^
  uploader.py

echo.
echo ===============================================
echo  Done! Executable is in the  dist\  folder.
echo  Copy the ADB folder next to the .exe when
echo  deploying to another machine.
echo ===============================================
pause
