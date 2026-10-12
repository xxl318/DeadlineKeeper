@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
title DeadlineKeeper 一键生成 Windows 软件

echo ======================================================
echo   DeadlineKeeper - 一键生成 Windows EXE / 安装包
echo ======================================================
echo.

REM 1. 查找 Python
set PY=
where py >nul 2>nul
if %errorlevel%==0 set PY=py -3
if not defined PY (
    where python >nul 2>nul
    if %errorlevel%==0 set PY=python
)
if not defined PY (
    echo [错误] 未检测到 Python。
    echo 请先安装 Python 3.8 或更高版本，并勾选 Add Python to PATH。
    echo 下载地址：https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)

for /f "tokens=*" %%v in ('%PY% --version') do echo [OK] %%v
echo.

REM 2. 创建虚拟环境，避免污染电脑已有 Python 环境
echo [1/4] 正在准备构建环境...
if not exist .venv (
    %PY% -m venv .venv
    if errorlevel 1 (
        echo [错误] 虚拟环境创建失败。
        pause
        exit /b 1
    )
)
call .venv\Scripts\activate.bat
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt
if errorlevel 1 (
    echo [错误] 依赖安装失败。请检查网络，或尝试清华源：
    echo python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    pause
    exit /b 1
)
echo      构建环境已准备好。
echo.

REM 3. 清理旧构建文件
echo [2/4] 正在清理旧文件...
if exist build rmdir /s /q build >nul 2>nul
if exist dist  rmdir /s /q dist  >nul 2>nul
if exist Output rmdir /s /q Output >nul 2>nul
if exist DeadlineKeeper.spec del DeadlineKeeper.spec >nul 2>nul
if exist build.log del build.log >nul 2>nul
echo      清理完成。
echo.

REM 4. 生成绿色版 EXE
echo [3/4] 正在生成绿色版 EXE，请稍等...
python -m PyInstaller --onefile --windowed ^
    --name "DeadlineKeeper" ^
    --icon "deadlinekeeper.ico" ^
    --hidden-import plyer.platforms.win.notification ^
    --collect-all openpyxl ^
    --collect-all PIL ^
    --add-data "assets/pet;assets/pet" ^
    --noconfirm ^
    deadlinekeeper.py > build.log 2>&1

if errorlevel 1 (
    echo [错误] PyInstaller 打包失败，请打开 build.log 查看原因。
    pause
    exit /b 1
)
echo      已生成：dist\DeadlineKeeper.exe
echo.

REM 5. 如安装 Inno Setup，则生成正式安装包
set ISCC=
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles(x86)%\Inno Setup 5\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 5\ISCC.exe"

if defined ISCC (
    echo [4/4] 检测到 Inno Setup，正在生成正式安装包...
    "%ISCC%" /Q installer.iss
    if errorlevel 1 (
        echo [提示] 安装包生成失败，但绿色版 EXE 已可使用：dist\DeadlineKeeper.exe
        explorer dist
        pause
        exit /b 0
    )
    echo.
    echo ======================================================
    echo   完成！
    echo.
    echo   绿色版：dist\DeadlineKeeper.exe
    echo   安装包：Output\DeadlineKeeper-Setup.exe
    echo ======================================================
    explorer Output
) else (
    echo [4/4] 未检测到 Inno Setup，已跳过正式安装包生成。
    echo.
    echo ======================================================
    echo   完成！
    echo.
    echo   绿色版：dist\DeadlineKeeper.exe
    echo.
    echo   如果想生成 Setup 安装包：
    echo   1. 安装 Inno Setup：https://jrsoftware.org/isdl.php
    echo   2. 重新运行本 bat 文件
    echo ======================================================
    explorer dist
)

echo.
pause
endlocal
