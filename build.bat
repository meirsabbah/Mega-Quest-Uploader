@echo off
setlocal

echo ============================================================
echo  NitzFlash - Full Build Script (Uploader + Video Encrypter)
echo ============================================================
echo.

:: VLC install this machine's build sources libvlc.dll/libvlccore.dll/plugins from
set VLC_SRC=C:\Program Files\VideoLAN\VLC
if not exist "%VLC_SRC%\libvlc.dll" (
    echo ERROR: VLC not found at expected path.
    echo Expected: %VLC_SRC%
    echo Install VLC ^(64-bit^) on this build machine first - it's needed to
    echo bundle the USB video player's LibVLC runtime.
    pause & exit /b 1
)

echo [1/7] Installing dependencies...
pip install pyinstaller sv-ttk pillow cryptography python-vlc --quiet

echo.
echo Detecting Python installation path...
for /f "tokens=*" %%i in ('python -c "import sys; print(sys.prefix)"') do set PYPREFIX=%%i
echo Python prefix: %PYPREFIX%

echo.
echo Closing any running instance...
taskkill /f /im "NitzFlash.exe" 2>nul
taskkill /f /im "client.exe" 2>nul
timeout /t 1 /nobreak >nul

echo.
echo [2/7] Cleaning previous build...
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build
if exist client rmdir /s /q client
if exist "NitzFlash.spec" del "NitzFlash.spec"
if exist "Video Encrypter\build" rmdir /s /q "Video Encrypter\build"
if exist "Video Encrypter\dist" rmdir /s /q "Video Encrypter\dist"

echo.
echo [3/7] Generating Video Encrypter\logo.ico...
python -c "from PIL import Image; img=Image.open('Video Encrypter/logo.png').convert('RGBA'); img.save('Video Encrypter/logo.ico', format='ICO', sizes=[(256,256),(128,128),(64,64),(32,32),(16,16)])"
if errorlevel 1 (
    echo WARNING: Could not generate logo.ico - client will use default icon.
    set CLIENT_ICO_FLAG=
) else (
    set CLIENT_ICO_FLAG=--icon logo.ico
)

echo.
echo [4/7] Building the USB video player  (client, folder build)...
pushd "Video Encrypter"
python -m PyInstaller --windowed ^
    --name client ^
    %CLIENT_ICO_FLAG% ^
    --add-data "logo.png;." ^
    client_player.py
if errorlevel 1 (
    popd
    echo ERROR: Failed to build the USB video player.
    pause & exit /b 1
)

echo Copying LibVLC runtime into dist\client\vlc_runtime...
if exist "dist\client\vlc_runtime" rmdir /s /q "dist\client\vlc_runtime"
mkdir "dist\client\vlc_runtime"
copy /y "%VLC_SRC%\libvlc.dll" "dist\client\vlc_runtime\" >nul
copy /y "%VLC_SRC%\libvlccore.dll" "dist\client\vlc_runtime\" >nul
xcopy /e /i /q "%VLC_SRC%\plugins" "dist\client\vlc_runtime\plugins" >nul
popd

echo.
echo [5/7] Building NitzFlash executable  (Quest Uploader + Video Encrypter hub)...
python -m PyInstaller --onefile --windowed ^
  --name "NitzFlash" ^
  --icon "icon.ico" ^
  --add-data "icon.ico;." ^
  --add-data "quest_uploader_logo.png;." ^
  --add-data "video_encrypter/logo.png;video_encrypter" ^
  --add-data "video_encrypter/logo_white.png;video_encrypter" ^
  --add-data "video_encrypter/instructions.pdf;video_encrypter" ^
  --add-data "%PYPREFIX%\tcl\tcl8.6;tcl" ^
  --add-data "%PYPREFIX%\tcl\tk8.6;tk" ^
  --collect-all tkinter ^
  --collect-all sv_ttk ^
  --collect-all cryptography ^
  main.py
if errorlevel 1 (
    echo ERROR: Failed to build NitzFlash.exe
    pause & exit /b 1
)

echo.
echo [6/7] Placing the USB player next to dist\NitzFlash.exe...
xcopy /e /i /q "Video Encrypter\dist\client" "dist\client" >nul

echo.
echo [7/7] Copying the USB player to the project root  (so `python main.py` finds it too)...
xcopy /e /i /q "Video Encrypter\dist\client" "client" >nul

echo.
echo ===============================================
echo  Done!
echo.
echo  dist\NitzFlash.exe   ^<-- the hub app (Quest Uploader + Video Encrypter)
echo  dist\client\         ^<-- USB player, already placed next to NitzFlash.exe
echo  client\              ^<-- same player, placed here so `python main.py` finds it too
echo.
echo  Still place the ADB\ folder next to NitzFlash.exe before deploying
echo  to another machine (Quest Uploader needs it).
echo ===============================================
pause
