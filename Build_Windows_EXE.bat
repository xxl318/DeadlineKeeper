@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
echo DeadlineKeeper - Windows build
set "PY="
python -c "import sys,venv,tkinter; assert sys.version_info >= (3,10)" >nul 2>&1
if not errorlevel 1 set "PY=python"
if defined PY goto python_ready
py -3 -c "import sys,venv,tkinter; assert sys.version_info >= (3,10)" >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if not defined PY (
 echo [ERROR] No working Python 3.10+ with tkinter and venv was found.
 goto failed
)
:python_ready
echo [OK] Using %PY%
%PY% --version
if "%~1"=="--probe" exit /b 0
echo [1/4] Preparing isolated build environment...
if not exist ".venv\Scripts\python.exe" goto create_venv
".venv\Scripts\python.exe" -c "import sys,tkinter,pip" >nul 2>&1
if not errorlevel 1 goto venv_ready
echo [ERROR] Existing .venv is invalid. Rename .venv and run again.
goto failed
:create_venv
%PY% -m venv ".venv"
if errorlevel 1 goto failed
:venv_ready
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
echo [2/4] Checking dependencies...
".venv\Scripts\python.exe" -c "import tkinter,PyInstaller,PIL,openpyxl,plyer"
if errorlevel 1 goto failed
echo [3/4] Building EXE... See build.log for details.
".venv\Scripts\python.exe" -m PyInstaller --onefile --windowed --name DeadlineKeeper --icon deadlinekeeper.ico --hidden-import plyer.platforms.win.notification --collect-all openpyxl --collect-all PIL --add-data "assets;assets" --noconfirm deadlinekeeper.py >build.log 2>&1
if errorlevel 1 goto failed
if not exist "dist\DeadlineKeeper.exe" goto failed
echo [OK] dist\DeadlineKeeper.exe
set "ISCC="
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC (
 echo [4/4] Inno Setup not installed; portable EXE is ready.
 goto done
)
echo [4/4] Building installer...
"%ISCC%" /Q installer.iss
if errorlevel 1 goto failed
echo [OK] Output\DeadlineKeeper-Setup.exe
:done
if /i not "%~1"=="--no-pause" pause
exit /b 0
:failed
echo [ERROR] Build stopped. Review the error above or build.log.
if /i not "%~1"=="--no-pause" pause
exit /b 1
