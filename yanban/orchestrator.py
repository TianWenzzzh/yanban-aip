"""研伴 Leader 编排器。

流程：自然语言任务 → LLM 分解为子任务 JSON（指定 Partner+输入）→ asyncio.gather 并发下发
→ 各子任务独立状态机轮询（含 awaiting-input 自动补参重试一次）→ LLM 聚合最终 Markdown 报告。

AIP 契约：与 Partner 之间走标准 AIP Direct RPC（AipRpcClient）。
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid

log = logging.getLogger("yanban.orchestrator")
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tomllib

from acps_sdk.aip import AipRpcClient, TaskState, TextDataItem

from llm import LLMError, chat, chat_json
from quality_gate import run_gate, GateReport

POLL_INTERVAL = 2.0
SUBTASK_TIMEOUT = 300.0


@dataclass
class SubTaskResult:
    partner: str
    state: str = "pending"
    output: str = ""
    error: str = ""
    duration_ms: int = 0
    rpc_calls: int = 0
    source: str = "static"  # W1：端点发现来源 static | discovery
    stage_key: str = ""     # W2：DAG stage 键（paper-pipeline 等工作流用）


@dataclass
class OrchestrationResult:
    task_id: str
    query: str
    status: str = "running"  # running | completed | failed
    plan: list[dict] = field(default_factory=list)
    subresults: list[SubTaskResult] = field(default_factory=list)
    final_report: str = ""
    gate: GateReport = field(default_factory=GateReport)
    error: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0


def load_partners(config_path: Path) -> tuple[list[dict], str]:
    raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
    partners = raw.get("partner", [])
    # 容器网络模式：YANBAN_NET=compose 时 endpoint 切换为 compose 服务名
    if os.environ.get("YANBAN_NET") == "compose":
        for p in partners:
            if p.get("compose_endpoint"):
                # 覆盖之前先留一份：`/identity` 那张表要同时报「宿主机端点」与「容器网络端点」，
                # 就地改掉 endpoint 会让两列永远相同（轮 141 看图才看见：六行两列一模一样）。
                p["host_endpoint"] = p["endpoint"]
                p["endpoint"] = p["compose_endpoint"]
    return partners, raw.get("leader", {}).get("aic", "yanban-leader")


DECOMPOSE_SYSTEM = (
    "你是科研任务编排助手。把用户的科研需求分解为 1-4 个可并发执行的子任务。"
    "可用的 Partner 及能力：\n{partner_hints}\n"
    '严格输出 JSON 数组：[{{"partner": "<partner名>", "input": "<给该Partner的完整任务文本>"}}]。'
    "input 必须是自包含的（Partner 看不到原对话）：需要网页就显式给出完整 URL；"
    "用户提供的代码块（如 ```csv 数据）、具体数字、URL 必须原样完整复制进对应子任务的 input，禁止省略、禁止用'见上文'代替。"
    "重要：若用户需求涉及多种能力（如既检索又写作、又设计实验），必须拆成多个子任务分别交给对应 Partner，不要合并给一个。"
    "只输出 JSON，不要任何其他文字。"
)

AGGREGATE_SYSTEM = (
    "你是科研报告撰写专家。把多个 Partner 的产出整合为一份结构化 Markdown 报告："
    "## 摘要（3 句内）→ ## 主要发现（按主题归纳，标明来自哪个 Partner）→ ## 结论与建议。"
    "忠实于 Partner 产出内容，不要编造；某子任务失败则在该主题下注明数据缺失。"
)


class Orchestrator:
    def __init__(self, partners_config: Path) -> None:
        self.partners, self.leader_aic = load_partners(partners_config)
        self._endpoints = {p["name"]: p["endpoint"] for p in self.partners}
        # W1 三层发现：DISCOVERY_URL 为空 = 纯静态模式（R18：行为与改造前完全一致）
        self.discovery_url = os.environ.get("DISCOVERY_URL", "").strip()
        self._hints = self._build_hints()
        self.tasks: dict[str, OrchestrationResult] = {}
        wf_path = partners_config.parent / "workflows.toml"
        self.workflows = tomllib.loads(wf_path.read_text(encoding="utf-8")).get("workflow", []) if wf_path.exists() else []

    def _build_hints(self) -> str:
        """Partner 能力提示；mcp_gateway 动态注入当前真实可用工具清单，防分解幻觉。"""
        lines = []
        for p in self.partners:
            hint = p["skill_hint"]
            if p["name"] == "mcp_gateway":
                tools = self._gateway_tool_ids(p["endpoint"].replace("/rpc", "/acs"))
                if tools:
                    hint += f" 当前真实可用工具 id（必须原样使用，不得虚构）：{tools}"
            lines.append(f"- {p['name']}: {hint}")
        return "\n".join(lines)

    @staticmethod
    def _gateway_tool_ids(acs_url: str) -> list[str]:
        import httpx

        try:
            r = httpx.get(acs_url, timeout=5)
            return [s["id"] for s in r.json().get("skills", [])]
        except Exception:  # noqa: BLE001
            return []

    # ── 对外入口 ─────────────────────────────────────────────────────────

    async def submit(self, query: str) -> str:
        task_id = f"orch-{uuid.uuid4().hex[:10]}"
        result = OrchestrationResult(task_id=task_id, query=query, started_at=asyncio.get_event_loop().time())
        self.tasks[task_id] = result
        asyncio.create_task(self._run(result))
        return task_id

    async def get(self, task_id: str) -> OrchestrationResult | None:
        return self.tasks.get(task_id)

    # ── 内部流程 ─────────────────────────────────────────────────────────

    def _match_workflow(self, query: str) -> list[dict] | None:
        """SOP 模板匹配：命中触发词返回确定性计划（跳过 LLM 分解，MetaGPT 式结构化契约）。"""
        import re

        for wf in self.workflows:
            if any(t in query for t in wf.get("triggers", [])):
                topic_m = re.search(r"主题[：:]\s*([^，。;；]+)", query)
                topic = topic_m.group(1).strip() if topic_m else query[:40]
                stages = []
                for st in wf.get("stages", []):
                    if st["partner"] in self._endpoints:
                        stage = {"partner": st["partner"], "input": st["input"].replace("{topic}", topic)}
                        if st.get("key"):
                            stage["key"] = st["key"]
                        if st.get("depends"):
                            stage["depends"] = list(st["depends"])
                        stages.append(stage)
                if stages:
                    return stages
        return None

    async def _run(self, result: OrchestrationResult) -> None:
        log.info("[run] start tid=%s pid=mark-new-code", result.task_id)
        try:
            result.plan = self._match_workflow(result.query) or await self._decompose(result.query)
            self._patch_plan_with_payloads(result)
        except LLMError as exc:
            # 分解失败降级：整句发给文献 Partner
            result.plan = [{"partner": "literature", "input": result.query}]
            result.error = f"分解降级：{exc}"
        except Exception as exc:  # noqa: BLE001
            result.status = "failed"
            result.error = f"分解异常：{exc}"
            result.finished_at = asyncio.get_event_loop().time()
            return

        result.subresults = await self._execute_plan(result.plan)

        aggregated = False
        try:
            result.final_report = await self._aggregate(result)
            aggregated = True
        except Exception as exc:  # noqa: BLE001
            result.status = "failed"
            result.error = f"聚合失败：{exc}"
        if aggregated:
            # 质量闭环：模拟审稿 → 不合格自动修订（创新点：评审迭代闭环）
            log.info("[gate] enabled=%s report_len=%s", result.gate.enabled, len(result.final_report))
            try:
                result.final_report, result.gate = await run_gate(result.query, result.final_report)
                log.info("[gate] done verdict=%s rounds=%s", result.gate.final_verdict, len(result.gate.rounds))
            except Exception as exc:  # noqa: BLE001
                log.info("[gate] ERROR: %s", exc)
                result.gate.final_verdict = f"gate-error: {str(exc)[:80]}"
        result.status = "completed" if aggregated else result.status
        result.finished_at = asyncio.get_event_loop().time()

    async def _execute_plan(self, plan: list[dict]) -> list[SubTaskResult]:
        """W2 DAG 执行器：stage 依赖拓扑序执行，上游产物注入下游输入。

        - stage 无 depends（LLM 分解路径）→ 全并行，行为与改造前一致
        - 检测到环 → 整体退化为并行（不阻断）
        - 依赖失败/超时 → 下游 skipped（诚实指标 W3 依赖此语义：烂底子的下游不算成功）
        """
        if not any(st.get("depends") for st in plan):
            return list(await asyncio.gather(*(self._run_subtask(st) for st in plan)))

        keys: list[str] = []
        for i, st in enumerate(plan):
            k = str(st.get("key") or st["partner"])
            keys.append(f"{k}#{i}" if keys.count(k) else k)

        def deps_of(i: int) -> list[str]:
            valid = set(keys)
            return [d for d in (plan[i].get("depends") or []) if d in valid]

        # 环检测（Kahn）：有环退化并行
        indeg = {keys[i]: len(deps_of(i)) for i in range(len(plan))}
        queue = [k for k, d in indeg.items() if d == 0]
        seen = 0
        dependents: dict[str, list[str]] = {}
        for i in range(len(plan)):
            for d in deps_of(i):
                dependents.setdefault(d, []).append(keys[i])
        while queue:
            k = queue.pop()
            seen += 1
            for nxt in dependents.get(k, []):
                indeg[nxt] -= 1
                if indeg[nxt] == 0:
                    queue.append(nxt)
        if seen != len(plan):
            log.warning("[dag] 检测到环，退化为并行执行")
            return list(await asyncio.gather(*(self._run_subtask(st) for st in plan)))

        pending = {keys[i]: i for i in range(len(plan))}
        done: set[str] = set()
        failed: set[str] = set()
        stage_outputs: dict[str, str] = {}
        subs: dict[str, SubTaskResult] = {}

        async def run_one(key: str) -> tuple[str, SubTaskResult]:
            i = pending[key]
            st = dict(plan[i])
            upstream = []
            for d in deps_of(i):
                if d in stage_outputs and stage_outputs[d]:
                    upstream.append(f"【上游产出——{d}】\n{stage_outputs[d][:2000]}")
            if upstream:
                st["input"] = (st["input"] + "\n\n以下是上游阶段产出，必须基于它完成任务：\n"
                               + "\n\n".join(upstream))
            sub = await self._run_subtask(st)
            sub.stage_key = key
            return key, sub

        while pending:
            progressed = False
            # 跳过被失败/被跳过上游阻塞的 stage（传递闭包：skipped 同样阻塞下游）
            for k in [k for k, i in pending.items() if failed & set(deps_of(i))]:
                subs[k] = SubTaskResult(partner=plan[pending[k]]["partner"], stage_key=k,
                                        state="skipped",
                                        error=f"上游 {sorted(set(deps_of(pending[k])) & failed)} 失败，跳过")
                failed.add(k)
                del pending[k]
                progressed = True
            runnable = [k for k, i in pending.items() if set(deps_of(i)) <= done]
            for key, sub in (await asyncio.gather(*(run_one(k) for k in runnable))) if runnable else []:
                subs[key] = sub
                done.add(key)
                del pending[key]
                progressed = True
                if sub.state in ("failed", "timeout"):
                    failed.add(key)
                else:
                    stage_outputs[key] = sub.output
            if not progressed:
                break  # 理论不可达（环已前置检测），防御性退出
        return [subs[k] for k in keys]

    @staticmethod
    def _normalize_input(raw: Any) -> str:
        """模型返回的 input 结构不稳定（str/dict/嵌套参数），规范化为自包含任务文本。"""
        import json as _json

        if isinstance(raw, str) and raw.strip():
            return raw.strip()
        if isinstance(raw, dict):
            for key in ("task", "query", "input", "text", "content", "prompt", "description"):
                v = raw.get(key)
                if isinstance(v, str) and v.strip():
                    return v.strip()
            if raw.get("arguments"):
                tool = raw.get("tool") or raw.get("name") or ""
                return f"{tool} {_json.dumps(raw['arguments'], ensure_ascii=False)}"
            return _json.dumps(raw, ensure_ascii=False)
        return str(raw)

    def _patch_plan_with_payloads(self, result: OrchestrationResult) -> None:
        """确定性兜底：LLM 分解常丢失代码块/URL，这里把原查询中的 payload 强制补进对应子任务。"""
        import re

        csv_blocks = re.findall(r"```csv.*?```", result.query, re.S)
        urls = re.findall(r"https?://[^\s，。\"']+", result.query)
        for st in result.plan:
            if st["partner"] == "analysis" and csv_blocks and "```csv" not in st["input"]:
                st["input"] = st["input"].rstrip() + "\n" + csv_blocks[0]
            if st["partner"] == "mcp_gateway" and urls and "http" not in st["input"]:
                st["input"] += f"\n目标 URL：{urls[0]}"

    @staticmethod
    def _looks_multi_ability(query: str) -> bool:
        """启发式：查询疑似包含多个独立能力（并/然后/同时/分号/序数词）。"""
        import re

        return bool(re.search(r"并|然后|同时|再|；|;|第一.{0,40}第二", query))

    async def _decompose(self, query: str) -> list[dict]:
        prompt = DECOMPOSE_SYSTEM.format(partner_hints=self._hints) + f"\n\n用户需求：{query}"
        plan = await chat_json(prompt, system="你是严谨的 JSON 输出器。", temperature=0.2, max_tokens=1024)
        valid = self._valid_plan(plan)
        # 韧性：多能力查询被合并成单任务时，带强调重试一次
        if len(valid) < 2 and self._looks_multi_ability(query):
            retry_prompt = (
                prompt
                + "\n\n注意：你上次的回答把多个独立能力合并成了一个子任务。"
                "该需求包含至少两个可并行的独立动作，必须拆成至少 2 个子任务，分别交给最合适的 Partner。"
            )
            plan = await chat_json(retry_prompt, system="你是严谨的 JSON 输出器。", temperature=0.2, max_tokens=1024)
            valid = self._valid_plan(plan)
        if not valid:
            raise LLMError("分解结果无有效子任务")
        return valid

    def _valid_plan(self, plan: Any) -> list[dict]:
        """规范化并过滤分解结果：partner 必须在注册表中，input 规范化为自包含文本。"""
        if not isinstance(plan, list):
            return []
        valid = []
        for st in plan[:4]:
            if isinstance(st, dict) and st.get("partner") in self._endpoints:
                text = self._normalize_input(st.get("input"))
                if text:
                    valid.append({"partner": st["partner"], "input": text})
        return valid

    async def _discover_fn(self, url: str, query: str, *, limit: int = 3):
        """发现函数注入点（测试覆写）；默认走梧桐 ADP 客户端。"""
        from discovery import discover
        return await discover(url, query, limit=limit)

    async def _resolve_endpoint(self, subtask_text: str, static_partner: str) -> tuple[str, str]:
        """W1 三层端点解析：返回 (endpoint, source)。

        层2 discovery：DISCOVERY_URL 非空时先查梧桐，候选本地重排后有可用端点即用之；
        层3 回落：Discovery 失败/无候选 → 静态 partners.toml（R18 兜底，永不阻断编排）。
        """
        if not self.discovery_url:
            return self._endpoints[static_partner], "static"
        try:
            from discovery import MIN_MATCH_SCORE, DiscoveryError, match_score
            candidates = await self._discover_fn(self.discovery_url, subtask_text[:200])
        except Exception as exc:  # noqa: BLE001 — Discovery 任何失败都降级，不阻断
            log.info("[discovery] 降级静态（%s）：%.80s", static_partner, exc)
            return self._endpoints[static_partner], "static"
        usable = [c for c in candidates
                  if c.endpoint and match_score(c, subtask_text) >= MIN_MATCH_SCORE]
        if usable:
            best = usable[0]
            log.info("[discovery] %s → %s（%s，score=%d）",
                     static_partner, best.endpoint, best.agent_name, match_score(best, subtask_text))
            return best.endpoint, "discovery"
        return self._endpoints[static_partner], "static"

    async def _run_subtask(self, st: dict) -> SubTaskResult:
        partner, text = st["partner"], st["input"]
        sub = SubTaskResult(partner=partner)
        endpoint, sub.source = await self._resolve_endpoint(text, partner)
        session_id, task_id = f"s-{uuid.uuid4().hex[:8]}", f"t-{uuid.uuid4().hex[:8]}"
        # T5.5：SDK 内部 httpx timeout=30s 硬编码——GLM 长生成（literature 实测 30-60s）全被杀
        # （bench 四指标抓包：literature 平均 30010ms = 超时实锤）。切慢 transport（300s）。
        from aip_slow import make_slow_client
        client = make_slow_client(endpoint, self.leader_aic)
        import time

        t0 = time.monotonic()
        try:
            task = await asyncio.wait_for(
                client.start_task(session_id=session_id, task_id=task_id, user_input=text),
                timeout=SUBTASK_TIMEOUT,
            )
            sub.rpc_calls += 1
            deadline = time.monotonic() + SUBTASK_TIMEOUT
            # 轮询过程态；awaiting-input 自动补一次"请按已知信息执行，缺失参数用合理默认值"
            while task.status.state in (TaskState.Accepted, TaskState.Working) and time.monotonic() < deadline:
                await asyncio.sleep(POLL_INTERVAL)
                task = await asyncio.wait_for(client.get_task(task_id=task_id, session_id=session_id), timeout=60)
                sub.rpc_calls += 1
            for rescue_round in range(2):
                if task.status.state != TaskState.AwaitingInput:
                    break
                # 代答携带原任务全文；第二轮追加执行指令强制满足缺参判定
                supplement = f"Leader 代答，原任务全文：{text}\n（缺失参数请用研究领域的合理默认值直接执行，并在结果中注明假设。）"
                if rescue_round == 1:
                    supplement += "\n请以上方补充后的完整文本为输入直接执行。"
                try:
                    task = await asyncio.wait_for(
                        client.continue_task(task_id=task_id, session_id=session_id, user_input=supplement),
                        timeout=120,
                    )
                except Exception as exc:  # noqa: BLE001
                    sub.state = "failed"
                    sub.error = f"代答失败：{str(exc)[:100]}"
                    break
                sub.rpc_calls += 1
                deadline = time.monotonic() + SUBTASK_TIMEOUT  # 每轮救援重置超时
                while task.status.state in (TaskState.Accepted, TaskState.Working) and time.monotonic() < deadline:
                    await asyncio.sleep(POLL_INTERVAL)
                    task = await asyncio.wait_for(client.get_task(task_id=task_id, session_id=session_id), timeout=60)
                    sub.rpc_calls += 1

            sub.state = str(task.status.state)
            if task.status.state == TaskState.AwaitingCompletion:
                sub.output = self._collect_products(task)
                task = await asyncio.wait_for(client.complete_task(task_id=task_id, session_id=session_id), timeout=60)
                sub.rpc_calls += 1
                sub.state = str(task.status.state)
            elif task.status.state != TaskState.Completed:
                sub.error = self._collect_data_items(task) or f"终态 {task.status.state}"
        except asyncio.TimeoutError:
            sub.state = "timeout"
            sub.error = "子任务超时"
        except Exception as exc:  # noqa: BLE001
            sub.state = "failed"
            sub.error = str(exc)
        finally:
            sub.duration_ms = int((time.monotonic() - t0) * 1000)
            await client.close()
        return sub

    @staticmethod
    def _collect_products(task: Any) -> str:
        parts: list[str] = []
        for p in task.products or []:
            for item in p.dataItems or []:
                if isinstance(item, TextDataItem) and item.text:
                    parts.append(item.text)
        return "\n\n".join(parts)

    @staticmethod
    def _collect_data_items(task: Any) -> str:
        parts: list[str] = []
        for item in (task.status.dataItems if task.status else None) or []:
            if isinstance(item, TextDataItem) and item.text:
                parts.append(item.text)
        return "\n".join(parts)

    async def _aggregate(self, result: OrchestrationResult) -> str:
        blocks = []
        for i, sub in enumerate(result.subresults, 1):
            plan = result.plan[i - 1] if i - 1 < len(result.plan) else {}
            body = sub.output or f"（无产出：{sub.error}）"
            blocks.append(
                f"### 子任务{i}（partner={sub.partner}，状态={sub.state}，耗时={sub.duration_ms}ms，RPC={sub.rpc_calls}）\n"
                f"任务：{plan.get('input', '')[:150]}\n\n{body[:4000]}"
            )
        prompt = "\n\n".join(blocks)
        try:
            text = await chat(
                f"用户原始需求：{result.query}\n\n各 Partner 产出：\n{prompt}",
                system=AGGREGATE_SYSTEM,
                temperature=0.3,
                max_tokens=3000,
            )
        except LLMError:
            text = ""
        if not text.strip():
            # 确定性兜底：聚合模型失败/为空时直接拼装子任务产出，保证始终有报告
            text = (
                "# 研伴报告（聚合降级模式）\n\n## 摘要\n原始需求：" + result.query[:120]
                + "。聚合模型暂不可用，以下为各 Partner 原始产出。\n\n" + prompt
            )
        return text
