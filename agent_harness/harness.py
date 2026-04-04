"""核心编排引擎 - 支持串行、并行、失败回退、持久化"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from agent_harness.agent import Agent, AgentContext, AgentResult, AgentState
from agent_harness.monitor import ConsoleMonitor, Monitor
from agent_harness.pipeline import Pipeline, PipelineStep
from agent_harness.store import Store
from agent_harness.metrics import MetricsCollector
from agent_harness.git_integration import git_init, git_commit

logger = logging.getLogger("agent_harness")


@dataclass
class HarnessResult:
    pipeline_id: str
    success: bool
    step_results: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


class Harness:
    """多 Agent 编排引擎

    支持:
    - 线性执行
    - 单步重试（retry_policy）
    - 失败回退循环（on_fail_goto）：test 失败 → 回到 codegen 重新生成 → 再测试
    """

    def __init__(self, monitor: Monitor | None = None, metrics: MetricsCollector | None = None):
        self.monitor = monitor or ConsoleMonitor()
        self.store = Store()
        self.metrics = metrics or MetricsCollector()
        self._cancelled: set[str] = set()  # pipeline_id 集合

    def cancel(self, pipeline_id: str) -> None:
        """从外部取消正在运行的 pipeline"""
        self._cancelled.add(pipeline_id)

    async def run(
        self,
        pipeline: Pipeline,
        ctx: AgentContext | None = None,
        resume_from: str | None = None,  # pipeline_id，从该 pipeline 的断点继续
    ) -> HarnessResult:
        # 断点续跑：从已有 pipeline_id 恢复
        if resume_from:
            checkpoint = self.store.get_checkpoint(resume_from)
            if checkpoint:
                pipeline_id = resume_from
                ctx = ctx or AgentContext()
                ctx.pipeline_id = pipeline_id
                ctx.shared.update(checkpoint["context"].get("shared", {}))
                ctx.history = checkpoint["context"].get("history", [])
                start_step = checkpoint["resume_from_step"]
                logger.info("Resuming pipeline %s from step %d", pipeline_id[:8], start_step)
            else:
                logger.warning("No checkpoint found for %s, starting fresh", resume_from)
                resume_from = None

        if not resume_from:
            pipeline_id = str(uuid.uuid4())
            ctx = ctx or AgentContext()
            ctx.pipeline_id = pipeline_id
            start_step = 0
            self.store.save_pipeline_start(pipeline_id, pipeline.name, ctx.shared)
            await git_init(ctx.workspace)

        result = HarnessResult(pipeline_id=pipeline_id, success=True)
        loop_counts: dict[int, int] = {}
        i = start_step

        while i < len(pipeline.steps):
            # 检查取消
            if pipeline_id in self._cancelled:
                self._cancelled.discard(pipeline_id)
                result.success = False
                result.error = "Pipeline 已被取消"
                self.monitor.emit("pipeline_cancelled", pipeline.steps[i].agent, i, pipeline_id)
                break

            step = pipeline.steps[i]
            ctx.step_index = i

            # 并行步骤组
            if step.parallel:
                step_result = await self._execute_parallel(step, ctx, i, pipeline_id)
                agent_name = "+".join(a.name for a in step.parallel_agents)
            else:
                step_result = await self._execute_step(step, ctx, i, pipeline_id)
                agent_name = step.agent.name

            result.step_results.append({
                "step": i,
                "agent": agent_name,
                "result": step_result,
                "loop": loop_counts.get(i, 0),
            })

            self.store.save_step(
                pipeline_id, i, agent_name,
                step_result.success, step_result.error,
                step_result.data, step_result.duration,
                loop=loop_counts.get(i, 0),
            )

            # Git: 每步完成后自动 commit
            status = "ok" if step_result.success else "fail"
            await git_commit(ctx.workspace, f"step-{i}-{agent_name}-{status}")

            ctx.history.append({
                "step": i,
                "agent": agent_name,
                "success": step_result.success,
                "data": step_result.data,
                "error": step_result.error,
            })

            if step_result.success:
                i += 1
                continue

            # 失败了，检查是否有回退跳转
            if step.on_fail_goto:
                goto_index = pipeline.get_step_index(step.on_fail_goto)
                if goto_index is not None:
                    loop_key = i  # 用当前 step 作为循环计数 key
                    loop_counts[loop_key] = loop_counts.get(loop_key, 0) + 1

                    if loop_counts[loop_key] < step.max_loops:
                        # 把错误信息写入 ctx，让 CodeGen 知道为什么要重新生成
                        ctx.set("last_error", step_result.error)
                        ctx.set("last_failed_step", step.agent.name)
                        ctx.set("loop_count", loop_counts[loop_key])

                        self.monitor.emit(
                            "loop_back", step.agent, i, pipeline_id,
                            goto=step.on_fail_goto, loop=loop_counts[loop_key],
                            error=step_result.error,
                        )
                        self.metrics.inc("loop_back_total", agent=step.agent.name)
                        i = goto_index
                        continue
                    else:
                        self.monitor.emit(
                            "loop_exhausted", step.agent, i, pipeline_id,
                            max_loops=step.max_loops,
                        )

            # 没有回退或循环次数用完，按 abort_on_fail 决定
            if step.abort_on_fail:
                result.success = False
                result.error = f"Step {i} ({step.agent.name}) failed: {step_result.error}"
                self.monitor.emit("pipeline_aborted", step.agent, i, pipeline_id, error=result.error)
                # 保存断点，支持从此处续跑
                self.store.save_checkpoint(pipeline_id, i, {
                    "shared": ctx.shared,
                    "history": ctx.history,
                })
                break

            i += 1

        self.monitor.emit(
            "pipeline_complete",
            pipeline.steps[-1].agent if pipeline.steps else _NullAgent(),
            len(pipeline.steps) - 1,
            pipeline_id,
            success=result.success,
        )
        self.store.save_pipeline_end(pipeline_id, result.success, result.error)
        if result.success:
            self.store.delete_checkpoint(pipeline_id)
        # 记录指标
        self.metrics.inc("pipeline_runs_total")
        if result.success:
            self.metrics.inc("pipeline_success_total")
        else:
            self.metrics.inc("pipeline_failed_total")
        return result

    async def _execute_step(
        self, step: PipelineStep, ctx: AgentContext, index: int, pipeline_id: str
    ) -> AgentResult:
        agent = step.agent
        prev_result = ctx.history[-1] if ctx.history else None

        if step.condition and not step.condition(ctx, prev_result):
            agent.state = AgentState.SKIPPED
            self.monitor.emit("step_skipped", agent, index, pipeline_id)
            return AgentResult(success=True, data="skipped")

        # 输入契约校验
        if hasattr(agent, "io") and agent.io:
            errors = agent.io.validate_inputs(ctx.shared)
            if errors:
                self.monitor.emit(
                    "contract_violation", agent, index, pipeline_id,
                    errors=errors, phase="input",
                )
                # 警告但不阻断，允许可选输入
                logger.warning("Agent %s 输入校验: %s", agent.name, errors)

        attempt = 0
        last_result = AgentResult(success=False, error="no attempts made")

        while True:
            self.monitor.emit("step_start", agent, index, pipeline_id, attempt=attempt)
            last_result = await agent.execute(ctx)
            self.monitor.emit(
                "step_end", agent, index, pipeline_id,
                attempt=attempt, success=last_result.success,
                duration=last_result.duration, error=last_result.error,
            )
            self.metrics.observe("agent_duration_seconds", last_result.duration, agent=agent.name)

            if last_result.success:
                return last_result

            attempt += 1
            if not step.retry_policy.should_retry(attempt):
                break

            agent.state = AgentState.RETRYING
            self.monitor.emit("step_retry", agent, index, pipeline_id, attempt=attempt)
            await step.retry_policy.wait(attempt)

        return last_result

    async def _execute_parallel(
        self, step: PipelineStep, ctx: AgentContext, index: int, pipeline_id: str
    ) -> AgentResult:
        """并行执行多个 agent，每个 agent 拿到 ctx 的浅拷贝避免写冲突"""
        import copy
        agents = step.parallel_agents
        names = "+".join(a.name for a in agents)

        self.monitor.emit("parallel_start", agents[0], index, pipeline_id, agents=[a.name for a in agents])

        # 每个 agent 拿到独立的 shared 副本，workspace 共享
        async def _run_one(agent):
            agent_ctx = AgentContext(
                pipeline_id=ctx.pipeline_id,
                step_index=ctx.step_index,
                workspace=ctx.workspace,
                shared=copy.copy(ctx.shared),
                history=list(ctx.history),
            )
            return await agent.execute(agent_ctx)

        tasks = [_run_one(agent) for agent in agents]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_data = {}
        errors = []
        total_duration = 0.0

        for agent, result in zip(agents, results):
            if isinstance(result, Exception):
                errors.append(f"{agent.name}: {result}")
                self.monitor.emit("step_end", agent, index, pipeline_id, success=False, error=str(result), duration=0)
            else:
                if not result.success:
                    errors.append(f"{agent.name}: {result.error}")
                all_data[agent.name] = result.data
                total_duration = max(total_duration, result.duration)
                self.monitor.emit(
                    "step_end", agent, index, pipeline_id,
                    success=result.success, duration=result.duration, error=result.error,
                )

        success = len(errors) == 0
        self.monitor.emit("parallel_end", agents[0], index, pipeline_id, success=success, agents=[a.name for a in agents])

        return AgentResult(
            success=success,
            data=all_data,
            error="; ".join(errors) if errors else None,
            duration=total_duration,
        )


class _NullAgent(Agent):
    async def run(self, ctx):
        return AgentResult(success=True)
