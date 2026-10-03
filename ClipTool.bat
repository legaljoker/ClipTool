@echo off
REM Start ClipTool (double-click). Arguments are passed on, e.g.  ClipTool.bat quick video.mp4
cd /d "%~dp0"
if not exist .venv (
  echo ClipTool isn't installed yet - running the installer first.
  call install.bat
)
call .venv\Scripts\activate.bat
python -m cliptool %*
if "%~1"=="" pause
