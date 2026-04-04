FROM python:3.11-slim

WORKDIR /app

# 安装 git（workspace git 集成需要）
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

# 安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制代码
COPY agent_harness/ ./agent_harness/
COPY frontend/ ./frontend/
COPY pipelines/ ./pipelines/
COPY server.py agents.yaml ./

# 数据目录
RUN mkdir -p .harness_data/logs .harness_workspaces

EXPOSE 8080

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8080"]
