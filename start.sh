#!/bin/bash
set -e

PORT=${1:-8080}

# 创建虚拟环境（如果不存在）
if [ ! -d ".venv" ]; then
    echo "📦 创建虚拟环境..."
    python3 -m venv .venv
fi

source .venv/bin/activate

# 安装依赖
echo "📥 检查依赖..."
pip install -q fastapi uvicorn websockets pyyaml

# 启动服务
echo "🚀 启动 Agent Harness Dashboard: http://localhost:${PORT}"
uvicorn server:app --host 0.0.0.0 --port "${PORT}"
