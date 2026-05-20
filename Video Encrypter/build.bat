@echo off
setlocal

echo ============================================================
echo  ניצחה הרוח — Video USB Burner  ^|  Build Script
echo ============================================================
echo.

:: Use Python 3.11 directly (standard install with predictable DLL locations)
set PYTHON=C:\Users\meirs\AppData\Local\Programs\Python\Python311\python.exe

"%PYTHON%" --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python 3.11 not found at expected path.
    echo Expected: %PYTHON%
    pause & exit /b 1
)

echo [1/4] Installing dependencies...
"%PYTHON%" -m pip install cryptography Pillow pyinstaller --quiet
if errorlevel 1 (
    echo ERROR: pip install failed.
    pause & exit /b 1
)

echo [2/4] Generating logo.ico from logo.png...
"%PYTHON%" -c "from PIL import Image; img=Image.open('logo.png').convert('RGBA'); img.save('logo.ico', format='ICO', sizes=[(256,256),(128,128),(64,64),(32,32),(16,16)])"
if errorlevel 1 (
    echo WARNING: Could not generate logo.ico — executables will use default icon.
    set ICO_FLAG=
) else (
    set ICO_FLAG=--icon logo.ico
)

echo [3/4] Building client.exe  (the USB player)...
"%PYTHON%" -m PyInstaller --onefile --windowed ^
    --name client ^
    %ICO_FLAG% ^
    --add-data "logo.png;." ^
    client_player.py
if errorlevel 1 (
    echo ERROR: Failed to build client.exe
    pause & exit /b 1
)

echo [4/4] Building VideoUSBSetup.exe  (the burner)...
"%PYTHON%" -m PyInstaller --onefile --windowed ^
    --name VideoUSBSetup ^
    %ICO_FLAG% ^
    --add-data "logo.png;." ^
    setup_usb.py
if errorlevel 1 (
    echo ERROR: Failed to build VideoUSBSetup.exe
    pause & exit /b 1
)

echo.
echo Done!
echo.
echo  dist\VideoUSBSetup.exe   ^<-- run this to burn USB drives
echo  dist\client.exe          ^<-- copied to every USB automatically
echo.
echo  Keep both .exe files in the same folder.
echo.
pause
