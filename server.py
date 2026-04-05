"""FastAPI 后端 - Agent 管理 + Pipeline 执行 + WebSocket 实时推送"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent_harness import (
    AgentContext, AgentRegistry, Harness, Pipeline, ExponentialBackoff, FixedRetry, Store,
)
from agent_harness.monitor import Monitor, Event
from agent_harness.agents import CodeGenAgent, TestRunnerAgent, ReviewerAgent, DeployerAgent
from agent_harness.loader import load_agents_from_config
from agent_harness.metrics import MetricsCollector, high_failure_rate_alert, slow_pipeline_alert
from agent_harness.scheduler import Scheduler
from agent_harness.cleanup import cleanup_loop
from agent_harness.notify import Notifier
from agent_harness.tenant import TenantRegistry, Tenant


# --- 认证 ---
API_TOKEN = os.environ.get("HARNESS_API_TOKEN", "")  # 空字符串 = 不启用认证


async def verify_token(request: Request):
    """API Token 认证，通过环境变量 HARNESS_API_TOKEN 配置"""
    if not API_TOKEN:
        return  # 未配置 token，跳过认证
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {API_TOKEN}":
        raise HTTPException(status_code=401, detail="未授权")


# --- WebSocket 广播监控器 ---
class WebSocketMonitor(Monitor):
    def __init__(self):
        self.connections: list[WebSocket] = []
        self.events: list[dict] = []
        self._logger = logging.getLogger("agent_harness")
        self._store: Store | None = None
        try:
            self._store = Store()
        except Exception:
            pass

    async def add(self, ws: WebSocket):
        await ws.accept()
        self.connections.append(ws)

    def remove(self, ws: WebSocket):
        if ws in self.connections:
            self.connections.remove(ws)

    def on_event(self, event: Event) -> None:
        data = event.to_dict()
        self.events.append(data)
        # 写日志
        self._logger.info(
            "[%s] %s | agent=%s step=%d | %s",
            event.pipeline_id[:8], event.event_type.upper(),
            event.agent_name, event.step_index, event.data,
        )
        # 持久化
        if self._store:
            self._store.save_event(
                event.pipeline_id, event.event_type,
                event.agent_name, event.step_index, event.data,
            )
        # WebSocket 广播
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._broadcast(data))
        except RuntimeError:
            pass

    async def _broadcast(self, data: dict):
        dead = []
        for ws in self.connections:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.remove(ws)


monitor = WebSocketMonitor()
registry = AgentRegistry()
metrics = MetricsCollector()
metrics.add_alert(high_failure_rate_alert(threshold=50.0))
metrics.add_alert(slow_pipeline_alert(threshold_seconds=60.0))
scheduler = Scheduler(max_concurrent=2)
# 共享 harness 实例，用于取消正在运行的 pipeline
harness_instance = Harness(monitor=monitor, metrics=metrics)
notifier = Notifier()
tenant_registry = TenantRegistry()

# 多租户模式：通过环境变量 HARNESS_MULTI_TENANT=1 启用
MULTI_TENANT = os.environ.get("HARNESS_MULTI_TENANT", "") == "1"


def init_default_agents():
    """从 agents.yaml 加载，如果不存在则用默认配置"""
    config_path = Path("agents.yaml")
    if config_path.exists():
        loaded = load_agents_from_config(config_path, registry)
        if loaded:
            return
    # fallback: 硬编码默认 agent
    registry.register(CodeGenAgent(), description="代码生成 Agent", category="codegen")
    registry.register(TestRunnerAgent(fail_first_n=1), description="测试验证 Agent", category="test")
    registry.register(ReviewerAgent(), description="代码审查 Agent", category="review")
    registry.register(DeployerAgent(), description="部署 Agent", category="deploy")


init_default_agents()


# 从 DB 恢复动态注册的 agent
def _restore_dynamic_agents():
    _store = Store()
    for row in _store.list_dynamic_agents():
        if registry.get(row["name"]):
            continue
        if row["type"] == "remote":
            from agent_harness.agents.remote import RemoteAgent
            agent = RemoteAgent(
                name=row["name"], endpoint=row["endpoint"] or "",
                health_endpoint=row["health_endpoint"], timeout=row["timeout"] or 120.0,
            )
        elif row["type"] == "shell":
            from agent_harness.agents.remote import ShellAgent
            agent = ShellAgent(name=row["name"], command=row["command"] or "", timeout=row["timeout"] or 120.0)
        else:
            continue
        meta = registry.register(agent, description=row["description"] or "", category=row["category"] or "general")
        meta.config["_dynamic"] = True

_restore_dynamic_agents()


# --- FastAPI App ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    import logging as _logging
    import signal

    log_dir = Path(".harness_data/logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = _logging.FileHandler(log_dir / "harness.log")
    file_handler.setFormatter(_logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    _logging.getLogger("agent_harness").addHandler(file_handler)
    _logging.getLogger("agent_harness").setLevel(_logging.INFO)

    scheduler.set_executor(_execute_job)
    await scheduler.start()
    cleanup_task = asyncio.create_task(cleanup_loop(interval_minutes=30, max_age_hours=24, max_count=50))

    # 后台定时健康检查
    health_check_task = asyncio.create_task(_health_check_loop())

    # 配置热重载：监听 agents.yaml 变化
    hot_reload_task = asyncio.create_task(_watch_agents_config())

    # 优雅关闭：收到 SIGTERM/SIGINT 时等正在运行的任务完成
    shutdown_event = asyncio.Event()

    def _handle_signal():
        logging.getLogger("agent_harness").info("Shutdown signal received, waiting for running tasks...")
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _handle_signal)
        except NotImplementedError:
            pass  # Windows 不支持

    yield

    # 关闭时：停止接收新任务，等待正在运行的任务完成
    logging.getLogger("agent_harness").info("Shutting down gracefully...")
    cleanup_task.cancel()
    hot_reload_task.cancel()
    health_check_task.cancel()
    await scheduler.stop()
    logging.getLogger("agent_harness").info("Shutdown complete.")

app = FastAPI(lifespan=lifespan)


# --- Rate Limiting ---
import time as _time
_rate_limit_store: dict[str, list[float]] = {}
RATE_LIMIT_MAX = int(os.environ.get("HARNESS_RATE_LIMIT", "30"))  # 每分钟最大请求数
RATE_LIMIT_WINDOW = 60.0

@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    if request.url.path.startswith("/api/") and request.method == "POST":
        client_ip = request.client.host if request.client else "unknown"
        now = _time.time()
        hits = _rate_limit_store.setdefault(client_ip, [])
        # 清理过期记录
        hits[:] = [t for t in hits if now - t < RATE_LIMIT_WINDOW]
        if len(hits) >= RATE_LIMIT_MAX:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=429, content={"detail": "请求过于频繁，请稍后再试"})
        hits.append(now)
    return await call_next(request)


# --- WebSocket ---
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await monitor.add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        monitor.remove(ws)


# --- Agent 管理 API ---
@app.get("/api/agents")
async def list_agents():
    return [m.to_dict() for m in registry.list_all()]


@app.get("/api/agents/health")
async def agents_health():
    results = {}
    for meta in registry.list_all():
        try:
            healthy = await meta.agent.health_check()
        except Exception:
            healthy = False
        results[meta.name] = {"healthy": healthy, "enabled": meta.enabled, "category": meta.category}
    return results


@app.get("/api/agents/{name}")
async def get_agent(name: str):
    meta = registry.get_meta(name)
    if not meta:
        return {"error": f"Agent '{name}' not found"}, 404
    return meta.to_dict()


class AgentToggle(BaseModel):
    enabled: bool

@app.put("/api/agents/{name}/toggle", dependencies=[Depends(verify_token)])
async def toggle_agent(name: str, body: AgentToggle):
    if body.enabled:
        ok = registry.enable(name)
    else:
        ok = registry.disable(name)
    if not ok:
        return {"error": f"Agent '{name}' not found"}
    return {"name": name, "enabled": body.enabled}


class AgentConfig(BaseModel):
    config: dict

@app.put("/api/agents/{name}/config", dependencies=[Depends(verify_token)])
async def update_agent_config(name: str, body: AgentConfig):
    ok = registry.update_config(name, body.config)
    if not ok:
        return {"error": f"Agent '{name}' not found"}
    return {"name": name, "config": body.config}


class RegisterAgentRequest(BaseModel):
    name: str
    type: str = "remote"  # remote 或 shell
    endpoint: str | None = None  # remote 类型必填
    health_endpoint: str | None = None
    command: str | None = None  # shell 类型必填
    description: str = ""
    category: str = "general"
    timeout: float = 120.0

@app.post("/api/agents", dependencies=[Depends(verify_token)])
async def register_agent(body: RegisterAgentRequest):
    """动态注册 Remote 或 Shell Agent，持久化到 DB"""
    if registry.get(body.name):
        return {"error": f"Agent '{body.name}' 已存在"}

    if body.type == "remote":
        if not body.endpoint:
            return {"error": "Remote agent 需要提供 endpoint"}
        from agent_harness.agents.remote import RemoteAgent
        agent = RemoteAgent(
            name=body.name, endpoint=body.endpoint,
            health_endpoint=body.health_endpoint, timeout=body.timeout,
        )
    elif body.type == "shell":
        if not body.command:
            return {"error": "Shell agent 需要提供 command"}
        from agent_harness.agents.remote import ShellAgent
        agent = ShellAgent(name=body.name, command=body.command, timeout=body.timeout)
    else:
        return {"error": f"不支持的类型: {body.type}，仅支持 remote 和 shell"}

    meta = registry.register(agent, description=body.description, category=body.category)
    meta.config["_dynamic"] = True  # 标记为动态注册

    # 持久化
    store.save_dynamic_agent(
        body.name, body.type, body.endpoint, body.health_endpoint,
        body.command, body.description, body.category, body.timeout,
    )
    return {"registered": body.name, "type": body.type}


@app.delete("/api/agents/{name}", dependencies=[Depends(verify_token)])
async def delete_agent(name: str):
    """删除动态注册的 Agent（YAML 里的不能删）"""
    meta = registry.get_meta(name)
    if not meta:
        return {"error": f"Agent '{name}' not found"}
    if not meta.config.get("_dynamic"):
        return {"error": f"Agent '{name}' 是系统内置的，不能从页面删除"}
    registry.unregister(name)
    store.delete_dynamic_agent(name)
    return {"deleted": name}


# --- Pipeline 执行 API ---
class RunRequest(BaseModel):
    agents: list[str] | None = None
    prompt: str = ""
    loop_map: dict[str, str] | None = None   # { agentName: gotoAgentName }
    loop_max: dict[str, int] | None = None   # { agentName: maxLoops }


class ABTestRequest(BaseModel):
    prompt: str
    pipelines: list[str]  # 要对比的 pipeline YAML 文件列表


@app.post("/api/run/ab", dependencies=[Depends(verify_token)])
async def run_ab_test(body: ABTestRequest):
    """A/B 测试：同一个 prompt 并行跑多个 pipeline，对比结果"""
    from agent_harness.config import load_pipeline_config, build_pipeline_from_config
    import copy as _copy

    async def run_one(pipeline_file: str):
        config_path = Path("pipelines") / pipeline_file
        if not config_path.exists():
            return {"pipeline_file": pipeline_file, "error": "not found"}
        try:
            cfg = load_pipeline_config(config_path)
            local_registry = _copy.deepcopy(registry)
            pipeline = build_pipeline_from_config(cfg, local_registry)
        except Exception as e:
            return {"pipeline_file": pipeline_file, "error": str(e)}

        ctx = _create_isolated_ctx()
        ctx.set("prompt", body.prompt)
        result = await harness_instance.run(pipeline, ctx)
        return {
            "pipeline_file": pipeline_file,
            "pipeline_id": result.pipeline_id,
            "success": result.success,
            "steps": len(result.step_results),
            "error": result.error,
        }

    results = await asyncio.gather(*[run_one(f) for f in body.pipelines])
    return {"prompt": body.prompt, "results": list(results)}

@app.post("/api/run", dependencies=[Depends(verify_token)])
async def run_pipeline(body: RunRequest | None = None):
    monitor.events.clear()
    prompt = body.prompt if body else "实现一个加法函数"
    agent_names = body.agents if body and body.agents else None

    # 构建 pipeline（同之前逻辑）
    if agent_names:
        agents = registry.build_pipeline_agents(agent_names)
        if not agents:
            return {"error": "没有找到可用的 agent"}
        pipeline = Pipeline(name="custom-pipeline")
        loop_map = (body.loop_map or {}) if body else {}
        loop_max = (body.loop_max or {}) if body else {}
        for agent in agents:
            on_fail_goto = loop_map.get(agent.name)
            max_loops = loop_max.get(agent.name, 3)
            pipeline.add(agent, on_fail_goto=on_fail_goto, max_loops=max_loops)
    else:
        pipeline = _default_pipeline()

    ctx = _create_isolated_ctx()
    ctx.set("prompt", prompt)

    harness = harness_instance
    result = await harness.run(pipeline, ctx)

    # 通知
    _event = "pipeline_complete" if result.success else "pipeline_failed"
    asyncio.create_task(notifier.notify(_event, {
        "pipeline_id": result.pipeline_id, "success": result.success, "error": result.error,
    }))

    return {
        "pipeline_id": result.pipeline_id,
        "success": result.success,
        "workspace_id": ctx.get("_workspace_id", ""),
        "steps": [
            {
                "step": sr["step"],
                "agent": sr["agent"],
                "loop": sr.get("loop", 0),
                "result": {
                    "success": sr["result"].success,
                    "data": sr["result"].data,
                    "error": sr["result"].error,
                    "duration": sr["result"].duration,
                    "attempts": sr["result"].attempts,
                },
            }
            for sr in result.step_results
        ],
        "error": result.error,
    }


async def _health_check_loop():
    """后台定时健康检查，agent 不可用时主动告警"""
    _prev_status: dict[str, bool] = {}
    while True:
        await asyncio.sleep(30)  # 每 30 秒检查一次
        try:
            for meta in registry.list_all():
                try:
                    healthy = await meta.agent.health_check()
                except Exception:
                    healthy = False

                prev = _prev_status.get(meta.name, True)
                _prev_status[meta.name] = healthy

                # 状态变化时告警
                if prev and not healthy:
                    logging.getLogger("agent_harness").warning(
                        "Agent %s 健康检查失败，服务不可用", meta.name
                    )
                    asyncio.create_task(notifier.notify("agent_unhealthy", {
                        "agent": meta.name, "category": meta.category,
                        "message": f"Agent {meta.name} 不可用",
                    }))
                    # 推送到 WebSocket
                    asyncio.create_task(monitor._broadcast({
                        "type": "agent_unhealthy", "agent": meta.name,
                        "ts": __import__("time").time(),
                    }))
                elif not prev and healthy:
                    logging.getLogger("agent_harness").info(
                        "Agent %s 已恢复", meta.name
                    )
                    asyncio.create_task(notifier.notify("agent_recovered", {
                        "agent": meta.name, "message": f"Agent {meta.name} 已恢复",
                    }))
        except Exception as e:
            logging.getLogger("agent_harness").error("Health check loop error: %s", e)

        # 同时检查指标告警
        try:
            fired = metrics.check_alerts()
            for alert in fired:
                asyncio.create_task(notifier.notify("alert_fired", alert))
                asyncio.create_task(monitor._broadcast({
                    "type": "alert_fired", "ts": __import__("time").time(), **alert,
                }))
        except Exception:
            pass


async def _watch_agents_config():
    """监听 agents.yaml 变化，自动重新加载"""
    config_path = Path("agents.yaml")
    last_mtime = config_path.stat().st_mtime if config_path.exists() else 0
    while True:
        await asyncio.sleep(5)
        try:
            if not config_path.exists():
                continue
            mtime = config_path.stat().st_mtime
            if mtime != last_mtime:
                last_mtime = mtime
                logging.getLogger("agent_harness").info("agents.yaml changed, reloading...")
                # 清空并重新加载
                for name in [m.name for m in registry.list_all()]:
                    registry.unregister(name)
                loaded = load_agents_from_config(config_path, registry)
                logging.getLogger("agent_harness").info("Reloaded agents: %s", loaded)
        except Exception as e:
            logging.getLogger("agent_harness").error("Hot reload error: %s", e)


async def _execute_job(job) -> dict:
    """调度器执行函数"""
    pipeline = _default_pipeline()
    ctx = _create_isolated_ctx()
    ctx.set("prompt", job.prompt)

    harness = harness_instance
    result = await harness.run(pipeline, ctx)
    return {"success": result.success, "pipeline_id": result.pipeline_id, "error": result.error}


def _create_isolated_ctx() -> AgentContext:
    """每次 pipeline 运行创建独立的 workspace"""
    import uuid as _uuid
    run_id = _uuid.uuid4().hex[:12]
    workspace = Path(".harness_workspaces") / run_id
    workspace.mkdir(parents=True, exist_ok=True)
    ctx = AgentContext(workspace=workspace)
    ctx.set("_workspace_id", run_id)
    return ctx


def _default_pipeline() -> Pipeline:
    """构建默认 pipeline，每次创建新的 agent 实例避免并发污染"""
    import copy
    codegen = registry.get("CodeGen")
    test = registry.get("TestRunner")
    reviewer = registry.get("Reviewer")
    deployer = registry.get("Deployer")

    pipeline = Pipeline(name="dev-test-deploy")
    if codegen:
        pipeline.add(copy.deepcopy(codegen))
    if test:
        pipeline.add(copy.deepcopy(test), on_fail_goto="CodeGen", max_loops=3)
    if reviewer:
        pipeline.add(copy.deepcopy(reviewer), condition=lambda ctx, prev: prev is not None and prev.get("success", False))
    if deployer:
        pipeline.add(copy.deepcopy(deployer))
    return pipeline


@app.get("/api/events")
async def get_events():
    return monitor.events


# --- Pipeline 配置 API ---
from agent_harness.config import load_pipeline_config, build_pipeline_from_config

@app.get("/api/pipelines")
async def list_pipelines():
    """列出所有 YAML pipeline 配置，包含 steps 信息"""
    pipeline_dir = Path("pipelines")
    if not pipeline_dir.exists():
        return []
    configs = []
    for f in sorted(pipeline_dir.glob("*.yaml")):
        cfg = load_pipeline_config(f)
        # 提取 steps 中的 agent 名称列表
        steps = []
        for s in cfg.get("steps", []):
            agent = s.get("agent", "")
            if isinstance(agent, list):
                steps.append({"agents": agent, "parallel": True})
            else:
                steps.append({
                    "agent": agent,
                    "on_fail_goto": s.get("on_fail_goto"),
                    "max_loops": s.get("max_loops", 3),
                    "condition": s.get("condition"),
                    "approval": s.get("approval", False),
                })
        configs.append({
            "file": f.name,
            "name": cfg.get("name", f.stem),
            "description": cfg.get("description", ""),
            "default_prompt": cfg.get("default_prompt", ""),
            "steps": steps,
        })
    return configs


class RunYamlRequest(BaseModel):
    pipeline_file: str
    prompt: str = "实现一个加法函数"

@app.post("/api/run/yaml", dependencies=[Depends(verify_token)])
async def run_yaml_pipeline(body: RunYamlRequest):
    """从 YAML 配置运行 pipeline"""
    monitor.events.clear()

    config_path = Path("pipelines") / body.pipeline_file
    if not config_path.exists():
        return {"error": f"配置文件 {body.pipeline_file} 不存在"}

    try:
        cfg = load_pipeline_config(config_path)
        pipeline = build_pipeline_from_config(cfg, registry)
    except Exception as e:
        return {"error": f"配置解析失败: {e}"}

    ctx = _create_isolated_ctx()
    ctx.set("prompt", body.prompt)

    harness = harness_instance
    result = await harness.run(pipeline, ctx)

    return {
        "pipeline_id": result.pipeline_id,
        "success": result.success,
        "workspace_id": ctx.get("_workspace_id", ""),
        "steps": [
            {
                "step": sr["step"],
                "agent": sr["agent"],
                "loop": sr.get("loop", 0),
                "result": {
                    "success": sr["result"].success,
                    "data": sr["result"].data,
                    "error": sr["result"].error,
                    "duration": sr["result"].duration,
                    "attempts": sr["result"].attempts,
                },
            }
            for sr in result.step_results
        ],
        "error": result.error,
    }


# --- 历史记录 API ---
store = Store()

@app.get("/api/history")
async def list_history(limit: int = 20, offset: int = 0):
    return store.list_runs(limit=limit, offset=offset)


@app.get("/api/history/compare")
async def compare_runs(id1: str, id2: str):
    """对比两次 pipeline 运行结果"""
    run1 = store.get_run(id1)
    run2 = store.get_run(id2)
    if not run1 or not run2:
        return {"error": "one or both runs not found"}

    def summarize(run):
        steps = run.get("steps", [])
        return {
            "id": run["id"][:8],
            "success": bool(run.get("success")),
            "duration": round((run.get("finished_at") or 0) - (run.get("started_at") or 0), 2),
            "steps": len(steps),
            "loops": sum(s.get("loop", 0) for s in steps),
            "failed_steps": [s["agent_name"] for s in steps if not s.get("success")],
        }

    return {"run1": summarize(run1), "run2": summarize(run2)}


@app.get("/api/history/{pipeline_id}")
async def get_history(pipeline_id: str):
    run = store.get_run(pipeline_id)
    if not run:
        return {"error": "not found"}
    return run


# --- 指标和告警 API ---
@app.get("/api/metrics")
async def get_metrics():
    snapshot = metrics.snapshot()
    summary = metrics.compute_from_store(store)

    # Agent 维度指标
    agent_stats = {}
    runs = store.list_runs(limit=200)
    for run in runs:
        run_detail = store.get_run(run["id"])
        if not run_detail:
            continue
        for step in run_detail.get("steps", []):
            name = step["agent_name"]
            if name not in agent_stats:
                agent_stats[name] = {"runs": 0, "success": 0, "failed": 0, "total_duration": 0}
            agent_stats[name]["runs"] += 1
            if step.get("success"):
                agent_stats[name]["success"] += 1
            else:
                agent_stats[name]["failed"] += 1
            agent_stats[name]["total_duration"] += step.get("duration") or 0
    for name, s in agent_stats.items():
        s["avg_duration"] = round(s["total_duration"] / s["runs"], 2) if s["runs"] > 0 else 0
        s["success_rate"] = round(s["success"] / s["runs"] * 100, 1) if s["runs"] > 0 else 0

    # 最近 10 次运行趋势
    recent = []
    for r in runs[:10]:
        recent.append({
            "id": r["id"][:8],
            "success": bool(r.get("success")),
            "duration": round((r.get("finished_at") or 0) - (r.get("started_at") or 0), 1),
            "name": r.get("name", ""),
        })

    # 调度器状态
    sched = {
        "queue_size": scheduler.queue_size,
        "max_concurrent": scheduler.max_concurrent,
        "total_jobs": len(scheduler._jobs),
        "running_jobs": sum(1 for j in scheduler._jobs.values() if j.status == "running"),
    }

    return {
        **snapshot,
        "summary": summary,
        "agent_stats": agent_stats,
        "recent_runs": recent,
        "scheduler": sched,
        "cost": {
            "total_tokens": sum(v for k, v in snapshot.get("counters", {}).items() if "tokens_total" in k),
            "total_cost": round(sum(v for k, v in snapshot.get("counters", {}).items() if "cost_total" in k), 6),
        },
    }

@app.get("/api/alerts")
async def get_alerts():
    fired = metrics.check_alerts()
    # 主动推送告警到 webhook
    for alert in fired:
        asyncio.create_task(notifier.notify("alert_fired", alert))
    return fired


# --- 调度 API ---
class SubmitJobRequest(BaseModel):
    prompt: str = "实现一个加法函数"

@app.post("/api/jobs", dependencies=[Depends(verify_token)])
async def submit_job(body: SubmitJobRequest):
    """提交任务到队列"""
    job = scheduler.submit("dev-test-deploy", body.prompt)
    return job.to_dict()

@app.get("/api/jobs")
async def list_jobs(limit: int = 50):
    return [j.to_dict() for j in scheduler.list_jobs(limit)]

@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    job = scheduler.get_job(job_id)
    if not job:
        return {"error": "not found"}
    return job.to_dict()

@app.get("/api/scheduler/status")
async def scheduler_status():
    return {
        "queue_size": scheduler.queue_size,
        "max_concurrent": scheduler.max_concurrent,
        "total_jobs": len(scheduler._jobs),
    }


# --- Pipeline 取消 API ---
@app.post("/api/cancel/{pipeline_id}", dependencies=[Depends(verify_token)])
async def cancel_pipeline(pipeline_id: str):
    harness_instance.cancel(pipeline_id)
    return {"cancelled": pipeline_id}


# --- 人工审批 API ---
@app.get("/api/approvals")
async def list_approvals():
    return harness_instance.get_pending_approvals()

@app.post("/api/approve/{pipeline_id}")
async def approve_pipeline(pipeline_id: str, approved: bool = True):
    ok = harness_instance.approve(pipeline_id, approved)
    if not ok:
        return {"error": "没有找到等待审批的 pipeline"}
    return {"pipeline_id": pipeline_id, "approved": approved}


# --- Pipeline 版本管理 API ---
@app.get("/api/pipelines/{name}/versions")
async def list_pipeline_versions(name: str):
    return store.list_pipeline_versions(name)

@app.post("/api/pipelines/{name}/versions", dependencies=[Depends(verify_token)])
async def save_pipeline_version(name: str, version: str):
    """保存当前 pipeline YAML 为指定版本"""
    pipeline_dir = Path("pipelines")
    for f in pipeline_dir.glob("*.yaml"):
        cfg = load_pipeline_config(f)
        if cfg.get("name") == name:
            config_yaml = f.read_text()
            store.save_pipeline_version(name, version, config_yaml)
            return {"name": name, "version": version}
    return {"error": f"Pipeline '{name}' not found"}


@app.post("/api/resume/{pipeline_id}", dependencies=[Depends(verify_token)])
async def resume_pipeline(pipeline_id: str):
    """从断点续跑失败的 pipeline，恢复原始 pipeline 配置和 workspace"""
    checkpoint = store.get_checkpoint(pipeline_id)
    if not checkpoint:
        return {"error": "没有找到断点，该 pipeline 可能已成功完成或未保存断点"}

    # 根据 checkpoint 里的 pipeline_name 恢复正确的 pipeline
    pipeline_name = checkpoint["context"].get("pipeline_name", "")
    pipeline_dir = Path("pipelines")
    pipeline = None

    # 尝试从 YAML 配置恢复
    for f in pipeline_dir.glob("*.yaml"):
        cfg = load_pipeline_config(f)
        if cfg.get("name") == pipeline_name:
            import copy as _copy
            local_registry = _copy.deepcopy(registry)
            pipeline = build_pipeline_from_config(cfg, local_registry)
            break

    if not pipeline:
        pipeline = _default_pipeline()

    # 不创建新 workspace，harness.run 会从 checkpoint 恢复原始 workspace
    ctx = AgentContext()

    result = await harness_instance.run(pipeline, ctx, resume_from=pipeline_id)
    return {
        "pipeline_id": result.pipeline_id,
        "success": result.success,
        "resumed_from": pipeline_id,
        "workspace_id": ctx.get("_workspace_id", ""),
        "error": result.error,
    }


# --- Workspace Git API ---
from agent_harness.git_integration import git_diff, git_log

@app.get("/api/workspace/{run_id}/log")
async def workspace_git_log(run_id: str):
    ws = Path(".harness_workspaces") / run_id
    if not ws.exists():
        return {"error": "workspace not found"}
    commits = await git_log(ws)
    return {"commits": commits}

@app.get("/api/workspace/{run_id}/diff")
async def workspace_git_diff(run_id: str):
    ws = Path(".harness_workspaces") / run_id
    if not ws.exists():
        return {"error": "workspace not found"}
    diff = await git_diff(ws)
    return {"diff": diff}


@app.get("/api/workspace/{run_id}/files")
async def workspace_files(run_id: str):
    """列出 workspace 中的文件"""
    ws = Path(".harness_workspaces") / run_id
    if not ws.exists():
        return {"error": "workspace not found"}
    files = []
    for f in sorted(ws.rglob("*")):
        if f.is_file() and ".git" not in f.parts:
            rel = str(f.relative_to(ws))
            size = f.stat().st_size
            files.append({"path": rel, "size": size})
    return {"files": files}


@app.get("/api/workspace/{run_id}/file")
async def workspace_file_content(run_id: str, path: str):
    """读取 workspace 中某个文件的内容"""
    ws = Path(".harness_workspaces") / run_id
    target = (ws / path).resolve()
    # 安全检查：不允许路径穿越
    if not str(target).startswith(str(ws.resolve())):
        return {"error": "invalid path"}
    if not target.exists() or not target.is_file():
        return {"error": "file not found"}
    try:
        content = target.read_text(errors="replace")
        return {"path": path, "content": content, "size": target.stat().st_size}
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/workspace/{run_id}/download")
async def workspace_download(run_id: str):
    """打包下载整个 workspace 为 zip"""
    import io
    import zipfile
    from fastapi.responses import StreamingResponse

    ws = Path(".harness_workspaces") / run_id
    if not ws.exists():
        return {"error": "workspace not found"}

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(ws.rglob("*")):
            if f.is_file() and ".git" not in f.parts:
                zf.write(f, f.relative_to(ws))
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=workspace-{run_id[:8]}.zip"},
    )


@app.get("/api/workspace/{run_id}/download/{filepath:path}")
async def workspace_download_file(run_id: str, filepath: str):
    """下载 workspace 中的单个文件"""
    from fastapi.responses import FileResponse as _FileResponse

    ws = Path(".harness_workspaces") / run_id
    target = (ws / filepath).resolve()
    if not str(target).startswith(str(ws.resolve())):
        return {"error": "invalid path"}
    if not target.exists() or not target.is_file():
        return {"error": "file not found"}

    return _FileResponse(target, filename=target.name)


# --- Webhook 管理 API ---
class WebhookRequest(BaseModel):
    url: str
    name: str = ""
    events: list[str] | None = None

@app.post("/api/webhooks", dependencies=[Depends(verify_token)])
async def add_webhook(body: WebhookRequest):
    notifier.add_webhook(url=body.url, name=body.name, events=body.events)
    return {"added": body.url}

@app.get("/api/webhooks")
async def list_webhooks():
    return notifier.list_webhooks()


# --- 多租户 API（需要 HARNESS_MULTI_TENANT=1）---
class TenantRequest(BaseModel):
    id: str
    name: str
    token: str

@app.post("/api/tenants", dependencies=[Depends(verify_token)])
async def create_tenant(body: TenantRequest):
    if not MULTI_TENANT:
        return {"error": "多租户模式未启用，设置 HARNESS_MULTI_TENANT=1"}
    tenant = Tenant(id=body.id, name=body.name, token=body.token)
    tenant_registry.register(tenant)
    return {"id": tenant.id, "name": tenant.name}

@app.get("/api/tenants", dependencies=[Depends(verify_token)])
async def list_tenants():
    if not MULTI_TENANT:
        return {"error": "多租户模式未启用"}
    return [{"id": t.id, "name": t.name} for t in tenant_registry.list_all()]


# 静态文件
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
