# Agent Harness

多 Agent 编排框架，负责管理 agent 的生命周期、编排执行流程、处理失败重试、记录可观测性数据。
<img width="1249" height="496" alt="image" src="https://github.com/user-attachments/assets/f6ba3ea1-75c1-44b0-9ab7-4a0bb30c5476" />
<img width="1255" height="563" alt="image" src="https://github.com/user-attachments/assets/3df04a28-63a2-4ffc-bfda-9248052df342" />
<img width="1282" height="474" alt="image" src="https://github.com/user-attachments/assets/91519925-f99f-492b-8680-de79e036371e" />
<img width="1172" height="368" alt="image" src="https://github.com/user-attachments/assets/2effc65d-7cc4-40dc-b023-c65385416701" />
<img width="1239" height="585" alt="image" src="https://github.com/user-attachments/assets/79895509-aa3f-4e3b-b858-cf4e205e8d0a" />


## 快速开始

```bash
./start.sh
```

浏览器打开 `http://localhost:8080`

## 核心概念

**Harness** 是编排层，不关心 agent 内部逻辑，只管：
- 谁先跑、谁后跑
- 失败了怎么办（重试 / 回退 / 中止）
- 记录所有执行过程

**Agent** 是执行单元，继承 `Agent` 基类，实现 `run()` 方法即可接入。

**Pipeline** 定义 agent 的执行顺序和依赖关系。

## 接入 Agent

### 方式一：Python 类（进程内）

```python
from agent_harness import Agent, AgentContext, AgentResult

class MyAgent(Agent):
    async def run(self, ctx: AgentContext) -> AgentResult:
        # 从 workspace 读写文件
        ws = ctx.ensure_workspace()
        code = (ws / "solution.py").read_text()

        # 从 ctx 读取元信息
        prompt = ctx.get("prompt")
        last_error = ctx.get("last_error")  # 上次失败的错误信息

        # 执行逻辑...

        return AgentResult(success=True, data={"result": "..."})
```

### 方式二：HTTP 远程 Agent

远程 agent 需要实现两个接口：

**POST /run**（必须）

请求：
```json
{
  "prompt": "实现一个加法函数",
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

响应 HTTP 200 即视为健康。

### 方式三：Shell 命令

```yaml
# agents.yaml
agents:
  - name: Linter
    type: shell
    command: "python3 -m pylint {workspace}/solution.py"
    category: review
```

## 配置 Agent（agents.yaml）

```yaml
agents:
  - name: CodeGen
    module: agent_harness.agents.codegen
    class: CodeGenAgent
    description: 代码生成
    category: codegen
    config:
      mock: false
      model: gpt-4o-mini

  - name: TestRunner
    module: agent_harness.agents.test_runner
    class: TestRunnerAgent
    category: test
    config:
      mock: false

  - name: RemoteReviewer
    type: remote
    endpoint: http://reviewer-service:9000/run
    health_endpoint: http://reviewer-service:9000/health
    category: review
```

## 配置 Pipeline（pipelines/*.yaml）

```yaml
name: dev-test-deploy
description: 开发→测试→审查→部署 闭环流程

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
  #   abort_on_fail: true

  # 带重试
  # - agent: Deployer
  #   retry:
  #     strategy: exponential
  #     max_retries: 3
  #     base_delay: 1.0
```

## API 文档

### Agent 管理

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/agents | 列出所有 agent |
| GET | /api/agents/health | 健康检查 |
| GET | /api/agents/{name} | 获取单个 agent |
| PUT | /api/agents/{name}/toggle | 启用/禁用 |
| PUT | /api/agents/{name}/config | 更新配置 |

### Pipeline 执行

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/run | 运行 pipeline（同步等待） |
| POST | /api/run/yaml | 从 YAML 配置运行 |
| POST | /api/cancel/{pipeline_id} | 取消正在运行的 pipeline |
| GET | /api/pipelines | 列出所有 YAML 配置 |

### 任务队列

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/jobs | 提交异步任务 |
| GET | /api/jobs | 列出所有任务 |
| GET | /api/jobs/{id} | 查询任务状态 |
| GET | /api/scheduler/status | 调度器状态 |

### 历史和指标

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/history | 历史记录列表 |
| GET | /api/history/{pipeline_id} | 单次运行详情 |
| GET | /api/metrics | 指标快照 |
| GET | /api/alerts | 当前告警 |

### Workspace

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/workspace/{run_id}/log | Git commit 历史 |
| GET | /api/workspace/{run_id}/diff | 代码变更 diff |

### Webhook

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/webhooks | 注册 webhook |
| GET | /api/webhooks | 列出 webhook |

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| HARNESS_API_TOKEN | 空（不启用认证） | API Bearer Token |
| HARNESS_RATE_LIMIT | 30 | 每分钟最大 POST 请求数 |
| OPENAI_API_KEY | - | LLM API Key（CodeGen 真实模式） |

## 运行测试

```bash
source .venv/bin/activate
pytest tests/ -v
```

## 项目结构

```
agent_harness/
├── agent.py          # Agent 基类、AgentContext、AgentResult
├── harness.py        # 核心编排引擎
├── pipeline.py       # Pipeline 定义（串行/并行/回退）
├── registry.py       # Agent 注册中心
├── monitor.py        # 事件监控
├── store.py          # SQLite 持久化
├── scheduler.py      # 任务队列和调度
├── metrics.py        # 指标收集和告警
├── retry.py          # 重试策略
├── config.py         # YAML Pipeline 解析
├── loader.py         # 插件式 Agent 加载
├── contracts.py      # 输入输出契约
├── cleanup.py        # Workspace 自动清理
├── git_integration.py # Git 集成
├── notify.py         # Webhook 通知
└── agents/           # 内置 Agent 实现
    ├── codegen.py    # 代码生成（支持 LLM）
    ├── test_runner.py # 测试执行（支持 pytest）
    ├── reviewer.py   # 代码审查
    ├── deployer.py   # 部署
    └── remote.py     # 远程/Shell Agent
```
