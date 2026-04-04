#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# 加载 .env
if [ -f "$SCRIPT_DIR/.env" ]; then
    set -a
    source "$SCRIPT_DIR/.env"
    set +a
    echo "📄 已加载 $SCRIPT_DIR/.env"
fi

# 使用项目的虚拟环境
if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    source "$PROJECT_ROOT/.venv/bin/activate"
fi

# 检查依赖
pip install -q fastapi uvicorn httpx 2>/dev/null

echo "🚀 启动需求生成 Agent (port 9001)..."
uvicorn remote_agents.requirement_gen.server:app --host 0.0.0.0 --port 9001 &
PID1=$!

echo "🚀 启动需求评审 Agent (port 9002)..."
uvicorn remote_agents.requirement_review.server:app --host 0.0.0.0 --port 9002 &
PID2=$!

echo ""
echo "✅ 两个远程 Agent 已启动："
echo "   需求生成: http://localhost:9001 (health: http://localhost:9001/health)"
echo "   需求评审: http://localhost:9002 (health: http://localhost:9002/health)"
echo ""
echo "按 Ctrl+C 停止所有 Agent"

# 等待任一进程退出
trap "kill $PID1 $PID2 2>/dev/null; exit" INT TERM
wait
