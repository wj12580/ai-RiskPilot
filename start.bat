@echo off
chcp 65001 >nul
title RiskPilot - 风控策略分析师 AI 分身
echo.
echo ==========================================
echo    RiskPilot - 风控策略分析师 AI 分身
echo ==========================================
echo.

REM 检查 Python 是否安装
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到 Python，请先安装 Python 3.8 或更高版本
    echo 下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)

echo [1/4] 检测到 Python 版本:
python --version
echo.

REM 创建虚拟环境（如果不存在）
if not exist "venv" (
    echo [2/4] 创建虚拟环境...
    python -m venv venv
    if errorlevel 1 (
        echo [错误] 创建虚拟环境失败
        pause
        exit /b 1
    )
) else (
    echo [2/4] 虚拟环境已存在，跳过创建
)
echo.

REM 激活虚拟环境
echo [3/4] 激活虚拟环境并安装依赖...
call venv\Scripts\activate

REM 安装依赖
pip install -r requirements.txt >nul 2>&1
if errorlevel 1 (
    echo [警告] 依赖安装可能有问题，尝试继续...
)
echo 依赖检查完成
echo.

REM 启动应用
echo [4/4] 启动 RiskPilot 服务...
echo.
echo ==========================================
echo    服务启动中，请稍候...
echo    启动成功后请访问: http://127.0.0.1:5000
echo ==========================================
echo.
echo [INFO] Browser will be opened automatically.

set APP_URL=http://127.0.0.1:5000
echo [INFO] Auto open browser: %APP_URL%
start "" /b powershell -NoProfile -Command "Start-Sleep -Seconds 2; Start-Process '%APP_URL%'"
python app.py

REM 如果应用退出，暂停显示错误信息
if errorlevel 1 (
    echo.
    echo [错误] 应用启动失败，请检查错误信息
    pause
)
