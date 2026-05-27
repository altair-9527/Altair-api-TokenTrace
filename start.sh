#!/bin/bash
# Altair API 余额查询服务 - 一键启动脚本
# 用法：chmod +x start.sh && ./start.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# 检查 Python3
if ! command -v python3 &> /dev/null; then
    echo "❌ 未找到 python3，请先安装 Python 3.10+"
    exit 1
fi

# 创建虚拟环境（如果不存在）
if [ ! -d "venv" ]; then
    echo "📦 正在创建 Python 虚拟环境..."
    python3 -m venv venv
fi

# 激活虚拟环境
source venv/bin/activate

# 安装依赖
echo "📦 正在安装依赖..."
pip install -r requirements.txt -q

# 启动服务
echo ""
echo "🚀 Altair API 余额查询服务启动中..."
echo "   本地访问: http://localhost:29180"
echo "   外网访问: http://<服务器IP>:29180"
echo "   按 Ctrl+C 停止服务"
echo ""

python3 -m uvicorn app.main:app --host 0.0.0.0 --port 29180
