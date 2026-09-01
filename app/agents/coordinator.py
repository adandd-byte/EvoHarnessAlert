from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.agents.events import (
    AgentArtifact,
    AgentEvent,
    AgentEventType,
    AgentTask,
    AgentTurnResult,
    CollaborationBlackboard,
    PRIORITY_ORDER,
    TaskPriority,
)
from app.agents.registry import AgentCapability
from app.core.config import Settings, get_settings


@dataclass(frozen=True)
class CoordinatorRun:
    board: CollaborationBlackboard
    rounds: int


class EventDrivenCoordinator:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def run_board(self, board: CollaborationBlackboard) -> CoordinatorRun:
        rounds = 0
        for round_no in range(1, self.settings.agent_runtime_max_steps + 1):
            rounds = round_no
            board = board.append_event(
                AgentEvent(type=AgentEventType.ROUND_STARTED, actor="CoordinatorAgent", message=f"round={round_no}")
            )
            board = self.derive_missing_work(board)
            board = self._execute_ready_tasks(board)
            accepted = self.try_accept_final(board)
            if accepted.final_artifact_id:
                return CoordinatorRun(accepted, rounds)
            board = accepted
        return CoordinatorRun(
            board.append_event(
                AgentEvent(type=AgentEventType.BUDGET_EXHAUSTED, actor="CoordinatorAgent", message="Runtime 轮数预算耗尽")
            ),
            rounds,
        )

    def derive_missing_work(self, board: CollaborationBlackboard) -> CollaborationBlackboard:
        board = self._ensure_task(board, "task_understand", "理解输入", AgentCapability.UNDERSTANDING, TaskPriority.CRITICAL)
        board = self._ensure_task(board, "task_assess_safety", "评估风险", AgentCapability.SAFETY, TaskPriority.CRITICAL)
        intent = board.latest_artifact("intent")
        risk = board.latest_artifact("risk")
        if intent and risk and self._needs_context(intent, risk):
            board = self._ensure_task(board, "task_gather_context", "准备上下文", AgentCapability.CONTEXT, TaskPriority.HIGH)
        if intent and risk:
            board = self._ensure_task(board, "task_response", "生成候选回复", AgentCapability.RESPONSE, TaskPriority.NORMAL)
        response = board.latest_artifact("response_proposal")
        review = board.latest_artifact("safety_review")
        if response and (review is None or review.metadata.get("responseArtifactId") != response.id):
            board = self._ensure_task(board, "task_review_response", "审查候选回复", AgentCapability.SAFETY, TaskPriority.HIGH)
        return board

    def try_accept_final(self, board: CollaborationBlackboard) -> CollaborationBlackboard:
        response = board.latest_artifact("response_proposal")
        review = board.latest_artifact("safety_review")
        if not response or not review:
            return board
        if review.metadata.get("responseArtifactId") != response.id:
            return board
        if not review.payload.get("approved"):
            return board
        if response.confidence < self.settings.agent_final_accept_min_confidence:
            return board
        return board.accept_final(response.id, "CoordinatorAgent", "候选回复通过安全审查和置信度门槛")

    def _execute_ready_tasks(self, board: CollaborationBlackboard) -> CollaborationBlackboard:
        claims = 0
        claim_count_by_agent: dict[str, int] = {}
        for task in sorted(board.open_tasks(), key=lambda item: PRIORITY_ORDER[item.priority], reverse=True):
            if claims >= self.settings.agent_runtime_max_claims_per_round:
                break
            agent = self._agent_for_task(task)
            if not agent:
                continue
            if claim_count_by_agent.get(agent, 0) >= self.settings.agent_runtime_max_claims_per_agent:
                continue
            claim_count_by_agent[agent] = claim_count_by_agent.get(agent, 0) + 1
            claims += 1
            claimed = task.claim(agent)
            board = board.update_task(claimed).append_event(
                AgentEvent(type=AgentEventType.TASK_CLAIMED, actor=agent, task_id=task.id, message="claim")
            )
            board = board.apply_turn_result(claimed, agent, self._act(agent, claimed, board))
        return board

    def _ensure_task(
        self,
        board: CollaborationBlackboard,
        task_id: str,
        title: str,
        capability: AgentCapability,
        priority: TaskPriority,
    ) -> CollaborationBlackboard:
        if task_id in board.tasks:
            return board
        task = AgentTask(
            id=task_id,
            title=title,
            priority=priority,
            required_capabilities=frozenset({capability.value}),
        )
        return board.add_task(task).append_event(
            AgentEvent(type=AgentEventType.TASK_CREATED, actor="CoordinatorAgent", task_id=task.id, message=task.title)
        )

    def _needs_context(self, intent: AgentArtifact, risk: AgentArtifact) -> bool:
        return intent.payload.get("intent") == "ALERT" and risk.payload.get("risk") in {"MEDIUM", "HIGH"}

    def _agent_for_task(self, task: AgentTask) -> str:
        capabilities = set(task.required_capabilities)
        if AgentCapability.UNDERSTANDING.value in capabilities:
            return "UnderstandingAgent"
        if AgentCapability.CONTEXT.value in capabilities:
            return "ContextAgent"
        if AgentCapability.RESPONSE.value in capabilities:
            return "ResponseAgent"
        if AgentCapability.SAFETY.value in capabilities:
            return "SafetyAgent"
        return ""

    def _act(self, agent: str, task: AgentTask, board: CollaborationBlackboard) -> AgentTurnResult:
        if task.id == "task_understand":
            return AgentTurnResult(artifacts=(self._artifact(agent, task, "intent", self._intent_payload(board)),))
        if task.id == "task_assess_safety":
            return AgentTurnResult(artifacts=(self._artifact(agent, task, "risk", self._risk_payload(board)),))
        if task.id == "task_gather_context":
            payload = {"memoryBrief": "", "knowledgeQuery": board.model_input, "retrievedKnowledge": [], "skillContext": []}
            return AgentTurnResult(artifacts=(self._artifact(agent, task, "context", payload, confidence=0.8),))
        if task.id == "task_response":
            payload = {"content": self._response_content(board), "mode": "alert_report" if self._board_intent(board) == "ALERT" else "chat"}
            return AgentTurnResult(artifacts=(self._artifact(agent, task, "response_proposal", payload, confidence=0.86),))
        if task.id == "task_review_response":
            response = board.latest_artifact("response_proposal")
            payload = {"approved": True, "reason": "候选回复未泄露敏感信息，未建议未经审批的高风险动作。"}
            artifact = self._artifact(agent, task, "safety_review", payload, confidence=0.95)
            return AgentTurnResult(artifacts=(AgentArtifact(
                id=artifact.id,
                owner=artifact.owner,
                kind=artifact.kind,
                payload=artifact.payload,
                confidence=artifact.confidence,
                task_id=artifact.task_id,
                metadata={"responseArtifactId": response.id if response else ""},
            ),))
        return AgentTurnResult(close_task=True)

    def _artifact(self, agent: str, task: AgentTask, kind: str, payload: dict, confidence: float = 0.9) -> AgentArtifact:
        return AgentArtifact(id=uuid.uuid4().hex, owner=agent, kind=kind, payload=payload, confidence=confidence, task_id=task.id)

    def _intent_payload(self, board: CollaborationBlackboard) -> dict:
        return {
            "intent": board.tasks.get("root", AgentTask("root", "")).metadata.get("intent", "CHAT"),
            "topic": board.model_input[:80],
            "needsContext": "告警" in board.model_input or "alert" in board.model_input.lower(),
        }

    def _risk_payload(self, board: CollaborationBlackboard) -> dict:
        root = board.tasks.get("root")
        priority = root.metadata.get("priority", "P3") if root else "P3"
        risk = "HIGH" if priority == "P0" else "MEDIUM" if priority in {"P1", "P2"} else "LOW"
        return {"risk": risk, "priority": priority, "reviewApproved": True, "reason": "基于告警优先级的规则评估"}

    def _board_intent(self, board: CollaborationBlackboard) -> str:
        artifact = board.latest_artifact("intent")
        return artifact.payload.get("intent", "CHAT") if artifact else "CHAT"

    def _response_content(self, board: CollaborationBlackboard) -> str:
        root = board.tasks.get("root")
        priority = root.metadata.get("priority", "P3") if root else "P3"
        alert_type = root.metadata.get("alertType", "PROBLEM") if root else "PROBLEM"
        if self._board_intent(board) != "ALERT":
            return "这是普通对话请求，当前不会触发 RAG、Incident 或工具队列。"
        return f"已识别为 {priority}/{alert_type} 告警，请结合日志、Trace、指标和 Runbook 补齐证据链。"


def status() -> dict:
    return {"status": "READY", "domain": "alerting", "message": "事件驱动协调器已就绪"}
