@echo off
where pythonw >nul 2>nul && (
  start "" pythonw -B "%~dp0sg8_editor.py" %*
  exit /b
)
where py >nul 2>nul && (
  py -3 -B "%~dp0sg8_editor.py" %*
  if errorlevel 1 pause
  exit /b
)
python -B "%~dp0sg8_editor.py" %*
if errorlevel 1 pause
