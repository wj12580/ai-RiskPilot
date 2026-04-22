#!/bin/bash

# RiskPilot - 风控策略分析师 AI 分身
# 一键启动脚本 (Linux/Mac)

echo ""
echo "=========================================="
echo "   RiskPilot - 风控策略分析师 AI 分身"
echo "=========================================="
echo ""

# 检查 Python 是否安装
if ! command -v python3 &> /dev/null; then
    if ! command -v python &> /dev/null; then
        echo "[错误] 未检测到 Python，请先安装 Python 3.8 或更高版本"
        echo "下载地址: https://www.python.org/downloads/"
        exit 1
    fi
    PYTHON_CMD=python
else
    PYTHON_CMD=python3
fi

echo "[1/4] 检测到 Python 版本:"
$PYTHON_CMD --version
echo ""

# 创建虚拟环境（如果不存在）
if [ ! -d "venv" ]; then
    echo "[2/4] 创建虚拟环境..."
    $PYTHON_CMD -m venv venv
    if [ $? -ne 0 ]; then
        echo "[错误] 创建虚拟环境失败"
        exit 1
    fi
else
    echo "[2/4] 虚拟环境已存在，跳过创建"
fi
echo ""

# 激活虚拟环境
echo "[3/4] 激活虚拟环境并安装依赖..."
source venv/bin/activate

# 安装依赖
pip install -q -r requirements.txt
if [ $? -ne 0 ]; then
    echo "[警告] 依赖安装可能有问题，尝试继续..."
fi
echo "依赖检查完成"
echo ""

# 启动应用
echo "[4/4] 启动 RiskPilot 服务..."
echo ""
echo "=========================================="
echo "   服务启动中，请稍候..."
echo "   启动成功后请访问: http://127.0.0.1:5000"
echo "=========================================="
echo ""

python app.py

# 如果应用退出，显示错误信息
if [ $? -ne 0 ]; then
    echo ""
    echo "[错误] 应用启动失败，请检查错误信息"
    read -p "按回车键退出..."
fi
