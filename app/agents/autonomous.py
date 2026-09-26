"""告警域自主 Agent：带私有记忆、独立模型配置的"真 Agent"实现层。

参考 mindbridge-py 的 app/agents/autonomous.py 移植。

设计定位（与 coordinator.py 的关系）：
- coordinator.py 当前是"规则占位"版本：_act() 里全部是确定性逻辑，用来
  保证主链路可用、可测；它定义了黑板协作的"编排契约"。
- 本模块实现的是契约的"真 Agent 版"：每个 Agent 拥有
  1. 独立的模型画像（AgentModelRegistry，见 agent_models.py）；
  2. 私有记忆（Redis 隔离 key：agent:{name}:{session_id}，见 README 私有记忆节）；
  3. decide/act 两段式：先声明"我要不要认领这个任务、为什么"，再执行；
- 与用户确认过的取舍一致：意图/优先级/风险归一是确定性规则，不做成
  Agent；这里的 Agent 只覆盖真正需要证据收集与模型判断的环节
  （上下文准备、候选研判生成、安全审查）。

run_autonomous_turn() 提供一个最小黑板循环，演示/验证真 Agent 层如何
协作；后续可把它切换为 coordinator 的默认执行体（通过注入 services）。
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.agents.events import (
    AgentArtifact,
    AgentEvent,
    AgentEventType,
    AgentMessage,
    AgentTask,
    AgentTurnResult,
    CollaborationBlackboard,
    TaskPriority,
)
from app.agents.registry import AgentCapability, AgentDecision, AgentProfile
from app.core.config import Settings
from app.schemas.dtos import AiMessage
from app.services.agent_models import AgentModelRegistry
from app.services.ai import AiClient, PromptTemplates

if TYPE_CHECKING:
    from app.services.knowledge import KnowledgeService, SearchResult
    from app.services.memory import RedisShortTermMemoryStore


# 告警研判里"需要上下文"的信号词：出现即认为值得做 RAG 检索
ALERT_CONTEXT_WORDS = ("告警", "报警", "alert", "P0", "P1", "故障", "根因", "排查", "恢复", "超时", "失败率")


@dataclass
class AgentRuntimeServices:
    """自主 Agent 运行所需的服务集合；由 build_agent_services() 组装。"""

    settings: Settings
    model_registry: AgentModelRegistry
    memory: "RedisShortTermMemoryStore"                # 共享会话记忆（Redis）
    private_memory: "AgentPrivateMemory"               # Agent 私有记忆（隔离 key）
    knowledge: "KnowledgeService | None"               # RAG 知识检索（None 时降级为无证据模式）
    session_id: str                                    # 当前会话标识（Redis owner/session 维度）


class AgentPrivateMemory:
    """Agent 私有记忆：每个 Agent 用独立的 Redis key 记录自己的工作笔记。

    key 形如 agent:SafetyAgent:{session_id}。与共享会话记忆的区别：
    - 私有记忆只进不出：用于 Agent 之间的信息隔离（SafetyAgent 的风险记录
      不该直接变成客户端话术）；
    - 复用 RedisShortTermMemoryStore 的裁剪、TTL 与脱敏逻辑。
    """

    def __init__(self, settings: Settings):
        from app.services.memory import RedisShortTermMemoryStore

        self.store = RedisShortTermMemoryStore(settings)

    def load(self, agent_name: str, session_id: str) -> list[Any]:
        return self.store.load_recent(agent_name, session_id)

    def append(self, agent_name: str, session_id: str, content: str) -> None:
        self.store.append(agent_name, session_id, "system", content)


class BaseAutonomousAgent:
    """自主 Agent 基类：统一提供模型客户端、私有记忆与 artifact 构造工具。"""

    profile: AgentProfile  # 子类必须声明自己的画像（能力、系统提示、模型别名）

    def __init__(self, services: AgentRuntimeServices):
        self.services = services

    @property
    def name(self) -> str:
        return self.profile.name

    def client(self) -> AiClient:
        """拿到本 Agent 专属的模型客户端（独立 provider/温度/长度）。"""
        return self.services.model_registry.client_for(self.name)

    def private_memory(self) -> list[Any]:
        return self.services.private_memory.load(self.name, self.services.session_id)

    def remember(self, content: str) -> None:
        self.services.private_memory.append(self.name, self.services.session_id, content)

    def decide(self, task: AgentTask, board: CollaborationBlackboard) -> AgentDecision:
        """子类实现：声明是否认领该任务。"""
        raise NotImplementedError

    def act(self, task: AgentTask, board: CollaborationBlackboard) -> AgentTurnResult:
        """子类实现：执行任务并产出 artifact。"""
        raise NotImplementedError

    def _artifact(
        self,
        kind: str,
        payload: dict[str, Any],
        task: AgentTask,
        confidence: float = 1.0,
        metadata: dict[str, Any] | None = None,
    ) -> AgentArtifact:
        return AgentArtifact(
            id=f"{self.name}:{kind}:{uuid.uuid4().hex[:10]}",
            owner=self.name,
            kind=kind,
            payload=payload,
            confidence=confidence,
            task_id=task.id,
            metadata=metadata or {},
        )


class AlertContextAgent(BaseAutonomousAgent):
    """上下文 Agent：为本轮研判准备会话记忆、RAG 证据与技能约束。"""

    profile = AgentProfile(
        name="AlertContextAgent",
        capabilities=frozenset({AgentCapability.CONTEXT}),
        system_prompt=(
            "你是告警助手的 ContextAgent。你只负责为本轮协作提供上下文，"
            "包括会话记忆摘要、RAG 检索证据和 Runbook 技能约束；"
            "你不判断最终结论是否可采纳。"
        ),
        memory_policy="private_context_memory",
        model_profile="context",
        tool_permissions=frozenset({"redis.memory", "rag.retrieve", "skills.read"}),
    )

    def decide(self, task: AgentTask, board: CollaborationBlackboard) -> AgentDecision:
        if board.latest_artifact("context"):
            return AgentDecision(False, reason="context artifact already exists")
        if AgentCapability.CONTEXT.value in task.required_capabilities:
            return AgentDecision(True, 0.86, "task explicitly asks for context")
        if self._looks_like_alert(board.model_input):
            return AgentDecision(True, 0.82, "alert-like input needs memory, RAG, and runbook context")
        return AgentDecision(False, reason="context not necessary for current artifacts")

    def act(self, task: AgentTask, board: CollaborationBlackboard) -> AgentTurnResult:
        from app.services.memory import compact_history_for_prompt
        from app.services.skills import AlertSkillLibrary

        # 1) 会话记忆：取 Redis 近期消息并压缩成可入 prompt 的形式
        history = self.services.memory.load_recent("session", self.services.session_id)
        compacted = compact_history_for_prompt(history, board.model_input)
        memory_brief = self._summarize_memory(history, board.model_input)

        # 2) RAG 检索：告警样式的输入才值得查知识库；服务不可用时降级为空证据
        query = board.model_input[:60] if self._looks_like_alert(board.model_input) else ""
        retrieved: list["SearchResult"] = []
        if query and self.services.knowledge is not None:
            try:
                retrieved = self.services.knowledge.retrieve(query, self.services.settings.knowledge_top_k)
            except Exception:
                retrieved = []  # 知识库故障不应阻断研判，只损失证据

        # 3) 技能约束：把 alert_triage 的处置流程注入上下文
        skill_context = ""
        for item in AlertSkillLibrary.status_items():
            if item["name"] == "alert_triage":
                skill_context = "；".join(item["workflow"])
                break

        payload = {
            "memoryBrief": memory_brief,
            "modelHistory": compacted,
            "knowledgeQuery": query,
            "retrievedKnowledge": retrieved,
            "skillContext": skill_context,
            "privateMemoryKey": f"agent:{self.name}:{self.services.session_id}",
        }
        self.remember(f"context intent query={query}; retrieved={len(retrieved)}")
        return AgentTurnResult(
            artifacts=(self._artifact("context", payload, task, 0.88),),
            messages=(
                AgentMessage(
                    id=f"msg:{uuid.uuid4().hex[:10]}",
                    sender=self.name,
                    recipient="AlertResponseAgent",
                    task_id=task.id,
                    kind="CONTEXT_READY",
                    content=f"上下文就绪；检索到 {len(retrieved)} 条知识。",
                ),
            ),
        )

    def _looks_like_alert(self, text: str) -> bool:
        lowered = (text or "").lower()
        return any(word in lowered for word in ALERT_CONTEXT_WORDS)

    def _summarize_memory(self, history: list[Any], current_input: str) -> str:
        """把历史记忆压缩成 1-3 条要点；LLM 失败时回落到确定性拼接。"""
        from app.services.memory import summarize_history_for_memory

        fallback = summarize_history_for_memory(history, current_input)
        if not history:
            return "无相关历史记忆。"
        try:
            summary = self.client().complete([
                AiMessage(role="system", content=f"{self.profile.system_prompt}\n只输出 1-3 条中文记忆要点，不要输出根因结论。"),
                AiMessage(role="user", content=f"当前输入：\n{current_input}\n\n最近历史：\n{fallback}"),
            ]).strip()
            return summary[: max(120, self.services.settings.memory_summary_max_chars)] or fallback
        except Exception:
            return fallback or "无相关历史记忆。"


class AlertResponseAgent(BaseAutonomousAgent):
    """候选研判 Agent：根据黑板上的证据提出告警研判 prompt 方案。"""

    profile = AgentProfile(
        name="AlertResponseAgent",
        capabilities=frozenset({AgentCapability.RESPONSE}),
        system_prompt=(
            "你是告警助手的 ResponseAgent。你根据黑板上的意图、严重级别、上下文"
            "提出候选研判方案；最终是否采纳由 Coordinator 决定。"
        ),
        memory_policy="private_response_strategy",
        model_profile="response",
        tool_permissions=frozenset({"llm.response_plan"}),
    )

    def decide(self, task: AgentTask, board: CollaborationBlackboard) -> AgentDecision:
        if board.latest_artifact("response_proposal") and "revisionOf" not in task.metadata:
            return AgentDecision(False, reason="response proposal already exists")
        if not board.latest_artifact("intent") or not board.latest_artifact("risk"):
            return AgentDecision(False, reason="response needs intent and risk artifacts")
        if AgentCapability.RESPONSE.value in task.required_capabilities:
            return AgentDecision(True, 0.84, "explicit response task")
        return AgentDecision(False, reason="waiting for task dispatch")

    def act(self, task: AgentTask, board: CollaborationBlackboard) -> AgentTurnResult:
        context = board.latest_artifact("context")
        context_payload = context.payload if context else {}
        knowledge = context_payload.get("retrievedKnowledge") or []
        skill_context = context_payload.get("skillContext") or ""
        memory_brief = context_payload.get("memoryBrief") or "无相关历史记忆。"
        knowledge_context = "\n\n".join(f"- [{item.source}] {item.content}" for item in knowledge)

        # 告警研判模式：带知识证据与 Runbook 约束；否则走普通问答模式
        mode = "assessment" if knowledge_context or skill_context else "answer"
        messages = PromptTemplates.alert_assessment(board.model_input)
        messages.insert(0, AiMessage(role="system", content=(
            f"{self.profile.system_prompt}\n"
            f"候选方案 mode={mode}。\n私有记忆：\n{_format_private_memory(self.private_memory())}\n"
            f"记忆摘要：\n{memory_brief}\nRunbook 约束：{skill_context or '无'}"
        )))
        payload = {
            "messages": messages,
            "mode": mode,
            "knowledgeCount": len(knowledge),
            "responseAgent": self.name,
            "privateMemoryKey": f"agent:{self.name}:{self.services.session_id}",
        }
        self.remember(f"response mode={mode}; knowledge={len(knowledge)}")
        return AgentTurnResult(
            artifacts=(self._artifact("response_proposal", payload, task, 0.86),),
            messages=(
                AgentMessage(
                    id=f"msg:{uuid.uuid4().hex[:10]}",
                    sender=self.name,
                    recipient="AlertSafetyAgent",
                    task_id=task.id,
                    kind="REVIEW_REQUEST",
                    content="请审查候选研判方案。",
                ),
            ),
        )


# 敏感信息模式：候选回复里不该出现密钥/内网地址等（出现即拦截）
_SENSITIVE_PATTERN = re.compile(r"(sk-[A-Za-z0-9]{16,}|password\s*[=:]\s*\S+|Bearer\s+[A-Za-z0-9._-]+)", re.IGNORECASE)
# P0/P1 的回复里应该出现止血语义的词，否则视为"只分析不处置"
_MITIGATION_WORDS = ("止血", "回滚", "降级", "扩容", "重启", "限流", "切换", "摘除", "恢复")


class AlertSafetyAgent(BaseAutonomousAgent):
    """安全审查 Agent：审查候选研判是否安全、是否给出可执行的止血建议。"""

    profile = AgentProfile(
        name="AlertSafetyAgent",
        capabilities=frozenset({AgentCapability.SAFETY}),
        system_prompt=(
            "你是告警助手的 SafetyAgent。你独立审查候选研判：是否泄露敏感信息、"
            "是否把推测说成事实、是否缺少 P0/P1 必需的止血建议。"
            "你可以退回不合格的候选方案。"
        ),
        memory_policy="private_safety_ledger",
        model_profile="safety",
        tool_permissions=frozenset({"response.review", "rules.severity"}),
    )

    def decide(self, task: AgentTask, board: CollaborationBlackboard) -> AgentDecision:
        latest_response = board.latest_artifact("response_proposal")
        latest_review = board.latest_artifact("safety_review")
        if latest_response and (latest_review is None or latest_review.metadata.get("responseArtifactId") != latest_response.id):
            return AgentDecision(True, 0.95, "candidate response needs safety critique")
        return AgentDecision(False, reason="no unreviewed response proposal")

    def act(self, task: AgentTask, board: CollaborationBlackboard) -> AgentTurnResult:
        response = board.latest_artifact("response_proposal")
        if response is None:  # 理论上 decide 已挡住；防御式返回
            return AgentTurnResult(close_task=False)
        messages = response.payload.get("messages", [])
        combined = "\n".join(getattr(message, "content", str(message)) for message in messages)
        approved = True
        reasons: list[str] = []
        if _SENSITIVE_PATTERN.search(combined):
            approved = False
            reasons.append("候选方案包含疑似敏感凭据（token/password），必须移除后再审")
        severity = str(_risk_priority(board))
        if severity in {"P0", "P1"} and not any(word in combined for word in _MITIGATION_WORDS):
            approved = False
            reasons.append("P0/P1 研判必须包含止血/处置建议，当前只有现象描述")
        payload = {
            "approved": approved,
            "reason": "；".join(reasons) if reasons else "候选方案满足当前安全约束",
            "responseArtifactId": response.id,
            "severity": severity,
            "privateMemoryKey": f"agent:{self.name}:{self.services.session_id}",
        }
        kind = "safety_review" if approved else "critique"
        events: tuple[AgentEvent, ...] = ()
        follow_up_tasks: tuple[AgentTask, ...] = ()
        if not approved:
            events = (
                AgentEvent(
                    type=AgentEventType.REVISION_REQUESTED,
                    actor=self.name,
                    task_id=task.id,
                    artifact_id=response.id,
                    message=payload["reason"],
                ),
            )
            follow_up_tasks = (
                AgentTask(
                    id=f"task:revise-response:{uuid.uuid4().hex[:8]}",
                    title="修订不安全的候选研判方案",
                    description=payload["reason"],
                    priority=TaskPriority.CRITICAL,
                    required_capabilities=frozenset({AgentCapability.RESPONSE.value}),
                    created_by=self.name,
                    metadata={"kind": "response", "revisionOf": response.id},
                ),
            )
        self.remember(f"review approved={approved}; reason={payload['reason']}")
        return AgentTurnResult(
            artifacts=(self._artifact(kind, payload, task, 0.95, {"responseArtifactId": response.id}),),
            tasks=follow_up_tasks,
            events=events,
        )


class AlertCoordinatorAgent(BaseAutonomousAgent):
    """协调 Agent：维护任务板、预算与最终采纳；由事件循环驱动，不认领任务。"""

    profile = AgentProfile(
        name="AlertCoordinatorAgent",
        capabilities=frozenset({AgentCapability.COORDINATION}),
        system_prompt="你是告警助手的 CoordinatorAgent。你维护任务板、预算、安全门槛和最终采纳。",
        memory_policy="private_coordination_trace",
        model_profile="coordinator",
        tool_permissions=frozenset({"taskboard.write", "blackboard.accept"}),
    )

    def decide(self, task: AgentTask, board: CollaborationBlackboard) -> AgentDecision:
        return AgentDecision(False, reason="CoordinatorAgent is driven by the event loop, not by fixed workflow slots")

    def act(self, task: AgentTask, board: CollaborationBlackboard) -> AgentTurnResult:
        return AgentTurnResult(close_task=False)

    def root_task(self, board: CollaborationBlackboard) -> AgentTask:
        return AgentTask(
            id="task:root",
            title="处理本轮告警输入",
            description=board.user_input or board.model_input,
            priority=TaskPriority.CRITICAL if "P0" in (board.user_input or board.model_input or "") else TaskPriority.NORMAL,
            created_by=self.name,
            metadata={"kind": "root"},
        )

    def remember_acceptance(self, artifact_id: str, reason: str) -> None:
        self.remember(f"accepted={artifact_id}; reason={reason}")


# ---------------------------------------------------------------- 最小自主循环

# 本模块内置的自主 Agent 全集（确定性归一仍由调用方/黑板前置处理完成）
AUTONOMOUS_AGENTS = (AlertContextAgent, AlertResponseAgent, AlertSafetyAgent)


def build_agent_services(settings: Settings, session_id: str, db: Any = None) -> AgentRuntimeServices:
    """组装自主 Agent 运行所需服务（mock/ollama/openai 均可通过 settings 切换）。

    db 传 None 时知识检索不可用，Agent 会降级为"无 RAG 证据"模式继续工作。
    """
    from app.services.knowledge import KnowledgeService
    from app.services.memory import RedisShortTermMemoryStore

    return AgentRuntimeServices(
        settings=settings,
        model_registry=AgentModelRegistry(settings),
        memory=RedisShortTermMemoryStore(settings),
        private_memory=AgentPrivateMemory(settings),
        knowledge=KnowledgeService(db, settings) if db is not None else None,
        session_id=session_id,
    )


def run_autonomous_turn(services: AgentRuntimeServices, model_input: str, max_rounds: int = 8) -> CollaborationBlackboard:
    """用自主 Agent 跑一轮完整黑板协作（确定性归一由调用方预先发布）。

    返回结束时的黑板：若存在 final_artifact_id 即本轮成功采纳。
    """
    from app.agents.events import TaskStatus

    # 1) 确定性前置处理：意图/风险归一（规则，不消耗 Agent 预算）
    coordinator = AlertCoordinatorAgent(services)
    intent_kind = "ALERT" if any(word in model_input.lower() for word in ALERT_CONTEXT_WORDS) else "CHAT"
    intent = AgentArtifact(
        id=f"intent:{uuid.uuid4().hex[:10]}",
        owner=coordinator.name,
        kind="intent",
        payload={"intent": intent_kind, "topic": model_input[:80], "normalizedBy": "rule"},
        task_id="task:root",
    )
    priority = "P0" if "P0" in model_input.upper() else "P1" if "P1" in model_input.upper() else "P3"
    risk = AgentArtifact(
        id=f"risk:{uuid.uuid4().hex[:10]}",
        owner=coordinator.name,
        kind="risk",
        payload={
            "risk": "HIGH" if priority == "P0" else "MEDIUM" if priority == "P1" else "LOW",
            "priority": priority,
            "reviewApproved": True,
            "reason": "基于告警严重级别的规则评估（确定性）",
            "normalizedBy": "rule",
        },
        task_id="task:root",
    )
    # 2) 初始化黑板：root 任务 + 确定性事实 artifact
    board = CollaborationBlackboard(turn_id=uuid.uuid4().hex, model_input=model_input, user_input=model_input)
    root = coordinator.root_task(board)
    board = board.add_task(root).add_artifact(intent).add_artifact(risk)

    # 3) 实例化自主 Agent 池
    agents = [agent_cls(services) for agent_cls in AUTONOMOUS_AGENTS]

    # 4) 黑板循环：派生任务 -> decide -> act -> 发布产物，直到采纳或预算耗尽
    for _ in range(max_rounds):
        if board.final_artifact_id:
            break
        # 4.1) 任务派生（确定性规则，等价于 coordinator.derive_missing_work 的语义）
        board = _derive_missing_work(board)

        # 4.2) Agent 认领执行
        progressed = False
        pending = [task for task in board.tasks.values() if task.status == TaskStatus.OPEN]
        for task in pending:
            for agent in agents:
                decision = agent.decide(task, board)
                if not decision.claim:
                    continue
                result = agent.act(task, board)
                board = board.apply_turn_result(task, agent.name, result)
                progressed = True
                break
            review = board.latest_artifact("safety_review")
            if review is not None and review.payload.get("approved"):
                # 有审查通过的候选：Coordinator 采纳并结束本轮
                coordinator.remember_acceptance(review.id, "safety approved")
                board = board.accept_final(review.id, coordinator.name, "安全审查通过")
                break
        if not progressed:
            break  # 没有任务可推进（等不到证据/预算耗尽），避免空转
    return board


def _derive_missing_work(board: CollaborationBlackboard) -> CollaborationBlackboard:
    """按黑板当前产物派生缺失的任务（确定性，不消耗 Agent 预算）。

    - intent/risk 已具备 -> 派生候选研判任务；
    - 出现未审查的候选方案 -> 派生安全审查任务。
    """
    has_intent = board.latest_artifact("intent") is not None
    has_risk = board.latest_artifact("risk") is not None
    if has_intent and has_risk:
        board = _ensure_open_task(
            board, "task_response", "生成候选研判方案", AgentCapability.RESPONSE, TaskPriority.NORMAL
        )
    response = board.latest_artifact("response_proposal")
    review = board.latest_artifact("safety_review")
    if response is not None and (review is None or review.metadata.get("responseArtifactId") != response.id):
        critiques = board.artifacts_by_kind("critique")
        if len(critiques) >= 2:
            # 修订预算耗尽（同一候选被驳回 2 次以上）：不能再无限循环下去。
            # 降级策略：采纳当前候选但留下驳回原因，转人工复核——
            # 这比卡死任务板或静默丢弃结论都更符合值班场景。
            board = board.accept_final(
                response.id,
                "AlertCoordinatorAgent",
                f"多次修订未通过安全审查（{len(critiques)} 次驳回），降级采纳并转人工复核",
            )
        else:
            board = _ensure_open_task(
                board, "task_review_response", "审查候选研判方案", AgentCapability.SAFETY, TaskPriority.HIGH
            )
    return board


def _ensure_open_task(
    board: CollaborationBlackboard,
    task_id: str,
    title: str,
    capability: AgentCapability,
    priority: TaskPriority,
) -> CollaborationBlackboard:
    """确保黑板上存在一个指定 ID 的 OPEN 任务；不存在或已关闭则（重新）创建。"""
    from app.agents.events import TaskStatus

    existing = board.tasks.get(task_id)
    if existing is not None and existing.status == TaskStatus.OPEN:
        return board
    return board.add_task(
        AgentTask(
            id=task_id,
            title=title,
            description="",
            priority=priority,
            required_capabilities=frozenset({capability.value}),
            created_by="AlertCoordinatorAgent",
        )
    )


def _format_private_memory(items: list[Any]) -> str:
    if not items:
        return "无"
    return "\n".join(f"- {getattr(item, 'content', str(item))}" for item in items[-5:])


def _risk_priority(board: CollaborationBlackboard) -> str:
    artifact = board.latest_artifact("risk")
    return str(artifact.payload.get("priority", "P3")) if artifact else "P3"
