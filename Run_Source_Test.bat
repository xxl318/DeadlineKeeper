@echo off
chcp 65001 >nul
title DeadlineKeeper 源码测试

echo 正在检查 Python...
where py >nul 2>nul
if %errorlevel%==0 (
    set PY=py -3
) else (
    where python >nul 2>nul
    if %errorlevel%==0 (
        set PY=python
    ) else (
        echo 未检测到 Python。请先安装 Python，并勾选 Add Python to PATH。
        pause
        exit /b 1
    )
)

%PY% -m pip install --quiet --upgrade pip
%PY% -m pip install --quiet plyer
%PY% deadlinekeeper.py
pause
