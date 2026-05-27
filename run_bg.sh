#!/bin/bash
# Altair API 余额查询服务 - 后台运行脚本
# 用法：chmod +x run_bg.sh && ./run_bg.sh
# 停止：./run_bg.sh stop

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
PID_FILE="$SCRIPT_DIR/.pid"

if [ "$1" = "stop" ]; then
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            kill "$PID"
            rm -f "$PID_FILE"
            echo "✅ 服务已停止 (PID: $PID)"
        else
            rm -f "$PID_FILE"
            echo "⚠️ 进程已不存在，清理 PID 文件"
        fi
    else
        echo "⚠️ 未找到 PID 文件，服务可能未在运行"
    fi
    exit 0
fi

if [ "$1" = "status" ]; then
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            echo "✅ 服务运行中 (PID: $PID)"
        else
            echo "❌ 进程已停止 (PID 文件残留)"
        fi
    else
        echo "❌ 服务未运行"
    fi
    exit 0
fi

# 创建虚拟环境
if [ ! -d "venv" ]; then
    echo "📦 正在创建 Python 虚拟环境..."
    python3 -m venv venv
fi

source venv/bin/activate

echo "📦 正在安装依赖..."
pip install -r requirements.txt -q

# 后台启动
echo "🚀 正在后台启动服务..."
nohup python3 -m uvicorn app.main:app --host 0.0.0.0 --port 29180 > "$SCRIPT_DIR/server.log" 2>&1 &
echo $! > "$PID_FILE"

sleep 2

PID=$(cat "$PID_FILE")
if kill -0 "$PID" 2>/dev/null; then
    echo "✅ 服务已启动 (PID: $PID)"
    echo "   本地访问: http://localhost:29180"
    echo "   外网访问: http://<服务器IP>:29180"
    echo "   查看日志: tail -f $SCRIPT_DIR/server.log"
    echo "   停止服务: $0 stop"
    echo "   查看状态: $0 status"
else
    echo "❌ 启动失败，请查看日志: cat $SCRIPT_DIR/server.log"
    rm -f "$PID_FILE"
    exit 1
fi
