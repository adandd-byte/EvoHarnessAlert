from __future__ import annotations

import uuid
from typing import Any

from app.agents.events import AgentEvent, AgentEventType, AgentTask, CollaborationBlackboard, TaskPriority
from app.agents.coordinator import EventDrivenCoordinator
from app.agents.result import AgentRunResult, AgentStep
from app.core.config import Settings, get_settings
from app.core.enums import AlertType, Severity
from app.schemas.dtos import AiMessage
from app.services.alerting import normalize_alert_type, normalize_severity
from app.services.privacy import PrivacySanitizer


class EventDrivenAgentRuntimeService:
    """
    Lightweight event-driven runtime used by the alert harness.

    The current production ingest path still lives in AlertIngestService. This
    service provides a concrete runtime entry for conversational alert flows and
    keeps the blackboard/trace contract explicit.
    """

    def __init__(self, db: Any = None, settings: Settings | None = None):
        self.db = db
        self.settings = settings or get_settings()
        self.sanitizer = PrivacySanitizer()
        self.coordinator = EventDrivenCoordinator(self.settings)

    def run(
        self,
        user_input: str,
        *,
        user_id: int | None = None,
        session_id: str = "",
        labels: dict[str, Any] | None = None,
        annotations: dict[str, Any] | None = None,
    ) -> AgentRunResult:
        labels = labels or {}
        annotations = annotations or {}
        model_input = self.sanitizer.sanitize(user_input)
        explicit_priority = labels.get("severity") or annotations.get("severity")
        priority = normalize_severity(explicit_priority or _guess_priority(model_input))
        alert_type = normalize_alert_type(labels.get("alert_type") or labels.get("type") or annotations.get("alert_type") or _guess_alert_type(model_input))
        intent = "ALERT" if explicit_priority or _looks_like_alert(model_input) else "CHAT"
        risk_level = _risk_from_priority(priority)
        board = self._new_board(user_id, session_id, user_input, model_input, intent, priority, alert_type)
        coordinator_run = self.coordinator.run_board(board)
        accepted = coordinator_run.board.accepted_artifact()
        steps = [
            AgentStep(1, "CoordinatorAgent", "创建 Root Task 并初始化黑板", f"turn_id={board.turn_id}"),
            AgentStep(2, "UnderstandingAgent", "发布 Intent Artifact", f"intent={intent}, alert_type={alert_type.value}"),
            AgentStep(3, "SafetyAgent", "完成风险评估和安全门槛检查", f"risk={risk_level}, priority={priority.value}"),
            AgentStep(4, "EventDrivenCoordinator", "运行黑板任务调度并尝试最终采纳", f"rounds={coordinator_run.rounds}, final={bool(accepted)}"),
        ]
        response = accepted.payload.get("content", "") if accepted else (
            f"已识别为 {priority.value}/{alert_type.value} 告警，请结合日志、Trace、指标和 Runbook 补齐证据链。"
            if intent == "ALERT"
            else "这是普通对话请求，当前不会触发 RAG、Incident 或工具队列。"
        )
        return AgentRunResult(
            intent=intent,
            priority=priority,
            alert_type=alert_type,
            risk_level=risk_level,
            assessment={"confidence": 0.8, "finalAcceptMinConfidence": self.settings.agent_final_accept_min_confidence},
            retrieved_knowledge=[],
            response_messages=[AiMessage(role="assistant", content=response)],
            steps=steps,
            memory_brief="",
            collaboration_events=list(coordinator_run.board.events),
            collaboration_tasks=list(coordinator_run.board.tasks.values()),
            collaboration_artifacts=list(coordinator_run.board.artifacts),
        )

    def _new_board(
        self,
        user_id: int | None,
        session_id: str,
        user_input: str,
        model_input: str,
        intent: str,
        priority: Severity,
        alert_type: AlertType,
    ) -> CollaborationBlackboard:
        root = AgentTask(
            id="root",
            title="告警研判 Root Task",
            description="识别意图、优先级、告警类型，并决定是否进入上下文链路。",
            priority=TaskPriority.CRITICAL if priority in {Severity.P0, Severity.P1} else TaskPriority.NORMAL,
            metadata={"intent": intent, "priority": priority.value, "alertType": alert_type.value},
        )
        board = CollaborationBlackboard(
            turn_id=uuid.uuid4().hex,
            user_id=user_id,
            session_id=session_id,
            user_input=user_input,
            model_input=model_input,
        )
        return board.add_task(root).append_event(
            AgentEvent(
                type=AgentEventType.TASK_CREATED,
                actor="CoordinatorAgent",
                task_id=root.id,
                message=root.title,
                metadata=root.metadata,
            )
        )


def _looks_like_alert(text: str) -> bool:
    lowered = text.lower()
    return any(
        word in lowered
        for word in ["告警", "报警", "异常", "错误率", "超时", "不可用", "p0", "p1", "p2", "incident", "alert"]
    )


def _guess_priority(text: str) -> str:
    lowered = text.lower()
    if any(word in lowered for word in ["p0", "全站", "核心链路中断", "资损"]):
        return "P0"
    if any(word in lowered for word in ["p1", "critical", "不可用", "失败率"]):
        return "P1"
    if any(word in lowered for word in ["p3", "info", "恢复", "观察"]):
        return "P3"
    return "P3"


def _risk_from_priority(priority: Severity) -> str:
    if priority == Severity.P0:
        return "HIGH"
    if priority in {Severity.P1, Severity.P2}:
        return "MEDIUM"
    return "LOW"


def _guess_alert_type(text: str) -> str:
    lowered = text.lower()
    if any(word in lowered for word in ["发布", "变更", "deploy", "release"]):
        return "event"
    if any(word in lowered for word in ["cpu", "内存", "磁盘", "主机", "容器", "node"]):
        return "host"
    if any(word in lowered for word in ["订单", "支付", "核销率", "成功率", "业务"]):
        return "business"
    return "problem"


def status() -> dict:
    settings = get_settings()
    return {
        "status": "READY",
        "domain": "alerting",
        "runtime": "event_driven_multi_agent",
        "maxSteps": settings.agent_runtime_max_steps,
        "maxClaimsPerRound": settings.agent_runtime_max_claims_per_round,
        "maxClaimsPerAgent": settings.agent_runtime_max_claims_per_agent,
        "finalAcceptMinConfidence": settings.agent_final_accept_min_confidence,
    }
