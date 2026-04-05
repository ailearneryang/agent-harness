# Agent Harness

多 Agent 编排框架，负责管理 agent 的生命周期、编排执行流程、处理失败重试、记录可观测性数据。

## 快速开始

```bash
./start.sh
```

浏览器打开 `http://localhost:8080`

### Docker 部署

```bash
docker-compose up
```

## 核心概念

**Harness** 是编排层，不关心 agent 内部逻辑，只管：
- 谁先跑、谁后跑
- 失败了怎么办（重试 / 回退 / 中止）
- 记录所有执行过程

**Agent** 是执行单元，继承 `Agent` 基类实现 `run()` 方法，或通过 HTTP/Shell 接入。

**Pipeline** 定义 agent 的执行顺序和依赖关系，支持 YAML 配置。

## 接入 Agent

### 方式一：Python 类（进程内）

```python
from agent_harness import Agent, AgentContext, AgentResult

class MyAgent(Agent):
    async def run(self, ctx: AgentContext) -> AgentResult:
        ws = ctx.ensure_workspace()
        # 在 workspace 中读写文件
        # 执行逻辑...
        return AgentResult(success=True, data={"result": "..."})
```

### 方式二：HTTP 远程 Agent

远程 agent 需要实现两个接口：

**POST /run**（必须）

请求：
```json
{
  "prompt": "...",
  "workspace": "/path/to/workspace",
  "shared": {"last_error": null, "loop_count": 0},
  "last_error": null,
  "loop_count": 0
}
```

响应：
```json
{
  "success": true,
  "data": {"任意结果": "..."},
  "error": null
}
```

**GET /health**（可选，用于健康检查）

返回 HTTP 200 即视为健康。任何语言都能接入。

### 方式三：Shell 命令

```yaml
# agents.yaml
agents:
  - name: Linter
    type: shell
    command: "python3 -m pylint {workspace}/solution.py"
    category: review
```

### 方式四：前端动态添加

在 Dashboard 的 Agent 管理页点击「+ 添加 Agent」，支持添加 Remote 和 Shell 类型。动态添加的 agent 持久化到数据库，重启不丢失，可从页面删除。系统内置 agent（agents.yaml）不可删除。

## 配置 Agent（agents.yaml）

```yaml
agents:
  - name: CodeGen
    module: agent_harness.agents.codegen
    class: CodeGenAgent
    description: 代码生成
    category: codegen
    config:
      mock: true

  - name: RemoteReviewer
    type: remote
    endpoint: http://localhost:9002/run
    health_endpoint: http://localhost:9002/health
    category: review
```

## 配置 Pipeline（pipelines/*.yaml）

```yaml
name: dev-test-deploy
description: 开发→测试→审查→部署 闭环流程
default_prompt: 实现一个快速排序函数

steps:
  - agent: CodeGen

  - agent: TestRunner
    on_fail_goto: CodeGen   # 测试失败时回退到 CodeGen 重新生成
    max_loops: 3            # 最多循环 3 次

  - agent: Reviewer
    condition: prev_success  # 只有测试通过才执行

  - agent: Deployer

  # 并行执行
  # - agent: [TestRunner, Reviewer]

  # 带重试
  # - agent: Deployer
  #   retry:
  #     strategy: exponential
  #     max_retries: 3
```

## 远程 Agent 示例

项目包含两个远程 agent 示例（需求生成 + 需求评审）：

```bash
# 配置 LLM API Key
cp remote_agents/.env.example remote_agents/.env
# 编辑 .env 填入 API Key

# 启动远程 agent
./remote_agents/start_agents.sh

# 启动 harness
./start.sh
```

在 Dashboard 选择 `requirement-loop` pipeline 运行。

## API 文档

### Agent 管理

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/agents | 列出所有 agent |
| GET | /api/agents/health | 健康检查（后台每 30s 自动探活） |
| POST | /api/agents | 动态添加 Remote/Shell agent（持久化） |
| DELETE | /api/agents/{name} | 删除动态 agent（内置不可删） |
| PUT | /api/agents/{name}/toggle | 启用/禁用 |
| PUT | /api/agents/{name}/config | 更新配置 |

### Pipeline 执行

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/run | 运行 pipeline（支持自定义回退配置） |
| POST | /api/run/yaml | 从 YAML 配置运行 |
| POST | /api/run/ab | A/B 测试（同一 prompt 跑多个 pipeline） |
| POST | /api/cancel/{pipeline_id} | 取消运行中的 pipeline |
| POST | /api/resume/{pipeline_id} | 从断点续跑（恢复原始 pipeline 和 workspace） |
| GET | /api/pipelines | 列出所有 YAML 配置（含 steps 详情） |

### 任务队列

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/jobs | 提交异步任务（持久化） |
| GET | /api/jobs | 列出所有任务 |
| GET | /api/jobs/{id} | 查询任务状态 |
| GET | /api/scheduler/status | 调度器状态 |

### 历史和指标

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/history?limit=20&offset=0 | 历史记录（分页） |
| GET | /api/history/compare?id1=&id2= | 对比两次运行 |
| GET | /api/history/{pipeline_id} | 单次运行详情 |
| GET | /api/metrics | 指标（总览 + Agent 维度 + 趋势 + 调度器） |
| GET | /api/alerts | 当前告警（同时推送到 webhook） |

### Workspace

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/workspace/{run_id}/files | 文件列表 |
| GET | /api/workspace/{run_id}/file?path= | 文件内容 |
| GET | /api/workspace/{run_id}/download | 打包下载 zip |
| GET | /api/workspace/{run_id}/download/{path} | 下载单个文件 |
| GET | /api/workspace/{run_id}/log | Git commit 历史 |
| GET | /api/workspace/{run_id}/diff | 代码变更 diff |

### Webhook

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/webhooks | 注册 webhook |
| GET | /api/webhooks | 列出 webhook |

### 多租户（需 HARNESS_MULTI_TENANT=1）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/tenants | 创建租户 |
| GET | /api/tenants | 列出租户 |

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| HARNESS_API_TOKEN | 空（不启用认证） | API Bearer Token |
| HARNESS_RATE_LIMIT | 30 | 每分钟最大 POST 请求数 |
| HARNESS_MULTI_TENANT | 空 | 设为 1 启用多租户 |
| OPENAI_API_KEY | - | LLM API Key |

## 前端 Dashboard

- 📦 Agent 管理 — 卡片展示、健康检查（绿/红圆点）、启用/禁用、动态添加/删除
- 🔄 Pipeline — 预定义 pipeline 选择（带流程预览）/ 自定义编排（带回退设置）、DAG 可视化、实时状态、下载结果
- 📜 历史 — 运行记录（分页）、Workspace 文件浏览器、Git 历史、单文件/打包下载
- 📊 指标 — 总览卡片、最近运行柱状图、Agent 维度表格、调度器状态、告警
- 📋 日志 — WebSocket 实时事件流、自动滚动
- 🔔 Toast 通知 — pipeline 成功/失败、agent 不可用实时弹窗

## 运行测试

```bash
source .venv/bin/activate
pytest tests/ -v
```

## 项目结构

```
agent-harness/
├── agent_harness/           # 核心框架
│   ├── agent.py             # Agent 基类、Context、Result
│   ├── harness.py           # 编排引擎（串行/并行/回退/取消/断点续跑）
│   ├── pipeline.py          # Pipeline 定义
│   ├── registry.py          # Agent 注册中心
│   ├── monitor.py           # 事件监控
│   ├── store.py             # SQLite 持久化（含动态 agent）
│   ├── scheduler.py         # 任务队列（持久化）
│   ├── metrics.py           # 指标和告警
│   ├── retry.py             # 重试策略（固定/指数退避）
│   ├── config.py            # YAML Pipeline 解析
│   ├── loader.py            # 插件式 Agent 加载
│   ├── contracts.py         # 输入输出契约
│   ├── cleanup.py           # Workspace 自动清理
│   ├── git_integration.py   # Git 集成
│   ├── notify.py            # Webhook 通知
│   ├── tenant.py            # 多租户
│   └── agents/              # 内置 Agent
├── remote_agents/           # 远程 Agent 示例
│   ├── requirement_gen/     # 需求生成（支持 LLM）
│   ├── requirement_review/  # 需求评审（新能源汽车标准）
│   ├── .env.example         # 环境变量模板
│   └── start_agents.sh      # 启动脚本
├── frontend/                # Vue Dashboard
├── pipelines/               # Pipeline YAML 配置
├── tests/                   # 37 个测试用例
├── server.py                # FastAPI 后端
├── agents.yaml              # Agent 注册配置
├── docker-compose.yml       # Docker 部署
├── Dockerfile
├── start.sh                 # 一键启动
└── README.md
```
