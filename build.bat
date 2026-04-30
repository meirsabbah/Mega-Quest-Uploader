@echo off
echo Installing PyInstaller...
pip install pyinstaller

echo.
echo Building executable...
pyinstaller --onefile --windowed --name "Quest Mass Uploader" uploader.py

echo.
echo Done! Your executable is in the  dist\  folder.
echo Copy the ADB folder next to the .exe before running on another machine.
pause
