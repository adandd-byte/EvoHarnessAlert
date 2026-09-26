from __future__ import annotations

import uuid  # 生成唯一 ID
from dataclasses import dataclass  # 数据类

from app.agents.events import (
    AgentArtifact,  # 黑板产物
    AgentEvent,     # 黑板事件
    AgentEventType, # 事件类型
    AgentTask,      # 任务
    AgentTurnResult,  # 单轮结果
    CollaborationBlackboard,  # 黑板
    PRIORITY_ORDER, # 优先级排序权重
    TaskPriority,   # 优先级
)
from app.agents.registry import AgentCapability  # 能力枚举
from app.core.config import Settings, get_settings


@dataclass(frozen=True)
class CoordinatorRun:
    board: CollaborationBlackboard  # 运行结束后的黑板
    rounds: int                     # 实际运行的轮数


class EventDrivenCoordinator:
    # 事件驱动协调器：驱动黑板协作直到产出最终采纳结论
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()  # 全局配置

    def run_board(self, board: CollaborationBlackboard) -> CoordinatorRun:
        board = self.publish_normalized_facts(board)  # 先发布确定性事实
        rounds = 0  # 轮数计数
        for round_no in range(1, self.settings.agent_runtime_max_steps + 1):  # 受轮数预算约束
            rounds = round_no  # 记录当前轮数
            board = board.append_event(  # 登记本轮开始
                AgentEvent(type=AgentEventType.ROUND_STARTED, actor="CoordinatorAgent", message=f"round={round_no}")
            )
            board = self.derive_missing_work(board)  # 按现状派生缺失任务
            board = self._execute_ready_tasks(board) # 执行本轮可认领任务
            accepted = self.try_accept_final(board)  # 尝试采纳
            if accepted.final_artifact_id:  # 已采纳则成功返回
                return CoordinatorRun(accepted, rounds)
            board = accepted  # 否则进入下一轮
        return CoordinatorRun(
            board.append_event(  # 预算耗尽则登记并返回
                AgentEvent(type=AgentEventType.BUDGET_EXHAUSTED, actor="CoordinatorAgent", message="Runtime 轮数预算耗尽")
            ),
            rounds,
        )

    def publish_normalized_facts(self, board: CollaborationBlackboard) -> CollaborationBlackboard:
        # 确定性前置处理：意图/优先级/告警类型归一、风险评估。
        # 用规则即可完成，不值得消耗 Agent 认领预算，也不伪装成 Agent 决策。
        root = board.tasks.get("root")  # 取根任务
        metadata = root.metadata if root else {}  # 根任务元数据（runtime 归一后写入）
        intent_artifact = AgentArtifact(  # 发布意图产物
            id=uuid.uuid4().hex,
            owner="CoordinatorAgent",
            kind="intent",
            payload={
                "intent": metadata.get("intent", "CHAT"),       # 意图
                "priority": metadata.get("priority", "P3"),     # 优先级
                "alertType": metadata.get("alertType", "PROBLEM"),  # 告警类型
                "topic": board.model_input[:80],                # 主题（截断）
                "normalizedBy": "rule",                         # 归一方式：规则
            },
            task_id="root",
        )
        priority = metadata.get("priority", "P3")  # 取优先级
        risk_artifact = AgentArtifact(  # 发布风险产物
            id=uuid.uuid4().hex,
            owner="CoordinatorAgent",
            kind="risk",
            payload={
                "risk": "HIGH" if priority == "P0" else "MEDIUM" if priority in {"P1", "P2"} else "LOW",  # 风险映射
                "priority": priority,       # 优先级
                "reviewApproved": True,     # 规则评估默认通过
                "reason": "基于告警优先级的规则评估（确定性）",  # 理由
                "normalizedBy": "rule",     # 归一方式：规则
            },
            task_id="root",
        )
        return board.add_artifact(intent_artifact).add_artifact(risk_artifact)  # 发布两个产物

    def derive_missing_work(self, board: CollaborationBlackboard) -> CollaborationBlackboard:
        intent = board.latest_artifact("intent")  # 取意图产物
        risk = board.latest_artifact("risk")      # 取风险产物
        if intent and risk and self._needs_context(intent, risk):  # 告警且中高风险则需要上下文
            board = self._ensure_task(board, "task_gather_context", "准备上下文", AgentCapability.CONTEXT, TaskPriority.HIGH)
        if intent and risk:  # 意图+风险都具备则需要生成候选回复
            board = self._ensure_task(board, "task_response", "生成候选回复", AgentCapability.RESPONSE, TaskPriority.NORMAL)
        response = board.latest_artifact("response_proposal")  # 取候选回复
        review = board.latest_artifact("safety_review")        # 取安全审查
        if response and (review is None or review.metadata.get("responseArtifactId") != response.id):  # 候选未审查则派生审查任务
            board = self._ensure_task(board, "task_review_response", "审查候选回复", AgentCapability.SAFETY, TaskPriority.HIGH)
        return board

    def try_accept_final(self, board: CollaborationBlackboard) -> CollaborationBlackboard:
        response = board.latest_artifact("response_proposal")  # 候选回复
        review = board.latest_artifact("safety_review")        # 安全审查
        if not response or not review:  # 缺一不可
            return board
        if review.metadata.get("responseArtifactId") != response.id:  # 审查的不是当前候选
            return board
        if not review.payload.get("approved"):  # 审查未通过
            return board
        if response.confidence < self.settings.agent_final_accept_min_confidence:  # 置信度不足
            return board
        return board.accept_final(response.id, "CoordinatorAgent", "候选回复通过安全审查和置信度门槛")  # 最终采纳

    def _execute_ready_tasks(self, board: CollaborationBlackboard) -> CollaborationBlackboard:
        claims = 0  # 本轮认领计数
        claim_count_by_agent: dict[str, int] = {}  # 每个 Agent 的认领计数
        for task in sorted(board.open_tasks(), key=lambda item: PRIORITY_ORDER[item.priority], reverse=True):  # 按优先级降序
            if claims >= self.settings.agent_runtime_max_claims_per_round:  # 达到每轮上限则停止
                break
            agent = self._agent_for_task(task)  # 为任务选择 Agent
            if not agent:  # 无可用 Agent 则跳过
                continue
            if claim_count_by_agent.get(agent, 0) >= self.settings.agent_runtime_max_claims_per_agent:  # 单 Agent 超限则跳过
                continue
            claim_count_by_agent[agent] = claim_count_by_agent.get(agent, 0) + 1  # Agent 认领计数 +1
            claims += 1  # 本轮认领 +1
            claimed = task.claim(agent)  # 标记任务被认领
            board = board.update_task(claimed).append_event(  # 更新任务并登记认领事件
                AgentEvent(type=AgentEventType.TASK_CLAIMED, actor=agent, task_id=task.id, message="claim")
            )
            board = board.apply_turn_result(claimed, agent, self._act(agent, claimed, board))  # 执行并落地结果
        return board

    def _ensure_task(
        self,
        board: CollaborationBlackboard,
        task_id: str,
        title: str,
        capability: AgentCapability,
        priority: TaskPriority,
    ) -> CollaborationBlackboard:
        if task_id in board.tasks:  # 已存在则不重复创建
            return board
        task = AgentTask(  # 建任务
            id=task_id,
            title=title,
            priority=priority,
            required_capabilities=frozenset({capability.value}),
        )
        return board.add_task(task).append_event(  # 加任务并登记创建事件
            AgentEvent(type=AgentEventType.TASK_CREATED, actor="CoordinatorAgent", task_id=task.id, message=task.title)
        )

    def _needs_context(self, intent: AgentArtifact, risk: AgentArtifact) -> bool:
        # 告警意图 + 中/高风险才需要额外上下文
        return intent.payload.get("intent") == "ALERT" and risk.payload.get("risk") in {"MEDIUM", "HIGH"}

    def _agent_for_task(self, task: AgentTask) -> str:
        capabilities = set(task.required_capabilities)  # 取所需能力
        if AgentCapability.CONTEXT.value in capabilities:  # 上下文能力 -> ContextAgent
            return "ContextAgent"
        if AgentCapability.RESPONSE.value in capabilities:  # 回复能力 -> ResponseAgent
            return "ResponseAgent"
        if AgentCapability.SAFETY.value in capabilities:  # 安全能力 -> SafetyAgent
            return "SafetyAgent"
        return ""  # 无法匹配

    def _act(self, agent: str, task: AgentTask, board: CollaborationBlackboard) -> AgentTurnResult:
        # 规则占位的执行体（未接真实模型）：按任务类型返回对应产物
        if task.id == "task_gather_context":  # 上下文任务
            payload = {"memoryBrief": "", "knowledgeQuery": board.model_input, "retrievedKnowledge": [], "skillContext": []}
            return AgentTurnResult(artifacts=(self._artifact(agent, task, "context", payload, confidence=0.8),))
        if task.id == "task_response":  # 回复任务
            payload = {"content": self._response_content(board), "mode": "alert_report" if self._board_intent(board) == "ALERT" else "chat"}
            return AgentTurnResult(artifacts=(self._artifact(agent, task, "response_proposal", payload, confidence=0.86),))
        if task.id == "task_review_response":  # 安全审查任务
            response = board.latest_artifact("response_proposal")  # 取被审候选
            payload = {"approved": True, "reason": "候选回复未泄露敏感信息，未建议未经审批的高风险动作。"}
            artifact = self._artifact(agent, task, "safety_review", payload, confidence=0.95)
            return AgentTurnResult(artifacts=(AgentArtifact(  # 记录审查关联的候选
                id=artifact.id,
                owner=artifact.owner,
                kind=artifact.kind,
                payload=artifact.payload,
                confidence=artifact.confidence,
                task_id=artifact.task_id,
                metadata={"responseArtifactId": response.id if response else ""},
            ),))
        return AgentTurnResult(close_task=True)  # 未知任务默认关闭

    def _artifact(self, agent: str, task: AgentTask, kind: str, payload: dict, confidence: float = 0.9) -> AgentArtifact:
        return AgentArtifact(id=uuid.uuid4().hex, owner=agent, kind=kind, payload=payload, confidence=confidence, task_id=task.id)  # 建产物

    def _board_intent(self, board: CollaborationBlackboard) -> str:
        artifact = board.latest_artifact("intent")  # 取意图产物
        return artifact.payload.get("intent", "CHAT") if artifact else "CHAT"  # 返回意图


    def _response_content(self, board: CollaborationBlackboard) -> str:
        root = board.tasks.get("root")  # 取根任务
        priority = root.metadata.get("priority", "P3") if root else "P3"  # 优先级
        alert_type = root.metadata.get("alertType", "PROBLEM") if root else "PROBLEM"  # 告警类型
        if self._board_intent(board) != "ALERT":  # 非告警返回普通对话文案
            return "这是普通对话请求，当前不会触发 RAG、Incident 或工具队列。"
        return f"已识别为 {priority}/{alert_type} 告警，请结合日志、Trace、指标和 Runbook 补齐证据链。"  # 告警复盘文案


def status() -> dict:
    return {"status": "READY", "domain": "alerting", "message": "事件驱动协调器已就绪"}  # 协调器状态