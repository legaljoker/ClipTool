@echo off
REM ClipTool installer for Windows. Double-click this file.
setlocal
cd /d "%~dp0"
echo === ClipTool installer ===

where py >nul 2>nul
if %errorlevel%==0 (
  set PY=py -3
) else (
  where python >nul 2>nul
  if errorlevel 1 (
    echo Python is not installed.
    echo Get it from https://www.python.org/downloads/  and tick "Add python.exe to PATH".
    echo Or run:  winget install Python.Python.3.12
    pause
    exit /b 1
  )
  set PY=python
)

where ffmpeg >nul 2>nul
if not errorlevel 1 goto :have_ffmpeg
echo.
echo ffmpeg is not installed - ClipTool needs it.
set /p FF="Install it now with winget? [y/n] "
if /i not "%FF%"=="y" goto :no_winget
winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
echo.
echo IMPORTANT: close this window and double-click install.bat again so Windows finds ffmpeg.
pause
exit /b 0
:no_winget
echo Download ffmpeg from https://www.gyan.dev/ffmpeg/builds/ and add its bin folder to PATH.
:have_ffmpeg

if not exist .venv (
  echo Creating virtual environment...
  %PY% -m venv .venv
  if errorlevel 1 ( echo Could not create the virtual environment. & pause & exit /b 1 )
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip >nul
echo Installing ClipTool (this can take a few minutes)...
python -m pip install -e .
if errorlevel 1 ( echo Install failed - see the messages above. & pause & exit /b 1 )

echo.
set /p A="Install Claude AI support for smarter clip picks and titles? [y/n] "
if /i "%A%"=="y" python -m pip install anthropic
set /p B="Install high-accuracy speaker identification + AI expressions, about 2GB? [y/n] "
if /i "%B%"=="y" python -m pip install pyannote.audio transformers torch

if not exist config.yaml copy config.example.yaml config.yaml >nul
if not exist input mkdir input
if not exist output mkdir output
if not exist avatars mkdir avatars
if not exist logs mkdir logs

echo.
python -m cliptool doctor
echo.
echo Done. Double-click ClipTool.bat to start.
pause
