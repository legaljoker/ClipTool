@echo off
REM Open the ClipTool video editor in your web browser (double-click).
cd /d "%~dp0"
if not exist .venv (
  echo ClipTool isn't installed yet - running the installer first.
  call install.bat
)
call .venv\Scripts\activate.bat
python -m cliptool editor
pause
