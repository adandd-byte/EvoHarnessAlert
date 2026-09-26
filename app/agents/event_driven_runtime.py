from __future__ import annotations

import uuid  # 生成唯一 ID
from typing import Any  # 任意类型

from app.agents.events import AgentEvent, AgentEventType, AgentTask, CollaborationBlackboard, TaskPriority
from app.agents.coordinator import EventDrivenCoordinator
from app.agents.result import AgentRunResult, AgentStep
from app.core.config import Settings, get_settings
from app.core.enums import AlertType, Severity
from app.schemas.dtos import AiMessage
from app.services.alerting import normalize_alert_type, normalize_severity
from app.services.privacy import PrivacySanitizer


class EventDrivenAgentRuntimeService:
    # 事件驱动运行时：告警对话 / 排障的统一入口
    def __init__(self, db: Any = None, settings: Settings | None = None):
        self.db = db                             # 数据库会话（可为空）
        self.settings = settings or get_settings()  # 全局配置
        self.sanitizer = PrivacySanitizer()      # 输入脱敏器
        self.coordinator = EventDrivenCoordinator(self.settings)  # 协调器

    def run(
        self,
        user_input: str,
        *,
        user_id: int | None = None,
        session_id: str = "",
        labels: dict[str, Any] | None = None,
        annotations: dict[str, Any] | None = None,
    ) -> AgentRunResult:
        labels = labels or {}            # 标签缺省为空
        annotations = annotations or {}  # 注解缺省为空
        model_input = self.sanitizer.sanitize(user_input)  # 输入脱敏
        explicit_priority = labels.get("severity") or annotations.get("severity")  # 显式优先级
        priority = normalize_severity(explicit_priority or _guess_priority(model_input))  # 归一优先级
        alert_type = normalize_alert_type(labels.get("alert_type") or labels.get("type") or annotations.get("alert_type") or _guess_alert_type(model_input))  # 归一告警类型
        intent = "ALERT" if explicit_priority or _looks_like_alert(model_input) else "CHAT"  # 判定意图
        risk_level = _risk_from_priority(priority)  # 由优先级推风险
        board = self._new_board(user_id, session_id, user_input, model_input, intent, priority, alert_type)  # 建黑板
        coordinator_run = self.coordinator.run_board(board)  # 运行异步协调
        accepted = coordinator_run.board.accepted_artifact()  # 取采纳产物
        steps = [  # 记录确定性执行步骤
            AgentStep(1, "CoordinatorAgent", "输入脱敏并做确定性归一（正则/规则提取优先级、告警类型、意图）", f"intent={intent}, alert_type={alert_type.value}, priority={priority.value}"),
            AgentStep(2, "CoordinatorAgent", "发布确定性事实 artifact 到黑板（不占用 Agent 认领预算）", f"risk={risk_level}, normalizedBy=rule"),
            AgentStep(3, "ContextAgent / ResponseAgent", "按能力认领上下文准备与候选回复生成任务", f"rounds={coordinator_run.rounds}"),
            AgentStep(4, "SafetyAgent", "审查候选回复，通过后由 Coordinator 采纳", f"final={bool(accepted)}, minConfidence={self.settings.agent_final_accept_min_confidence}"),
        ]
        response = accepted.payload.get("content", "") if accepted else (  # 无采纳则用兜底文案
            f"已识别为 {priority.value}/{alert_type.value} 告警，请结合日志、Trace、指标和 Runbook 补齐证据链。"
            if intent == "ALERT"
            else "这是普通对话请求，当前不会触发 RAG、Incident 或工具队列。"
        )
        return AgentRunResult(  # 组装运行结果
            intent=intent,  # 意图
            priority=priority,  # 级别
            alert_type=alert_type,  # 类型
            risk_level=risk_level,  # 风险
            assessment={"confidence": 0.8, "finalAcceptMinConfidence": self.settings.agent_final_accept_min_confidence},  # 结论
            retrieved_knowledge=[],  # RAG 证据（占位）
            response_messages=[AiMessage(role="assistant", content=response)],  # 用户回复
            steps=steps,  # 执行步骤
            memory_brief="",  # 记忆摘要（占位）
            collaboration_events=list(coordinator_run.board.events),  # 事件流
            collaboration_tasks=list(coordinator_run.board.tasks.values()),  # 任务表
            collaboration_artifacts=list(coordinator_run.board.artifacts),  # 产物流
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
        root = AgentTask(  # 根任务：承载归一化结果
            id="root",
            title="告警研判 Root Task",
            description="识别意图、优先级、告警类型，并决定是否进入上下文链路。",
            priority=TaskPriority.CRITICAL if priority in {Severity.P0, Severity.P1} else TaskPriority.NORMAL,  # P0/P1 为紧急
            metadata={"intent": intent, "priority": priority.value, "alertType": alert_type.value},  # 归一结果
        )
        board = CollaborationBlackboard(  # 初始化黑板
            turn_id=uuid.uuid4().hex,
            user_id=user_id,
            session_id=session_id,
            user_input=user_input,
            model_input=model_input,
        )
        return board.add_task(root).append_event(  # 加入根任务并登记创建事件
            AgentEvent(
                type=AgentEventType.TASK_CREATED,  # 任务创建事件
                actor="CoordinatorAgent",
                task_id=root.id,
                message=root.title,
                metadata=root.metadata,
            )
        )


def _looks_like_alert(text: str) -> bool:
    lowered = text.lower()  # 转小写
    return any(  # 命中任一告警关键词即判定为告警
        word in lowered
        for word in ["告警", "报警", "异常", "错误率", "超时", "不可用", "p0", "p1", "p2", "incident", "alert"]
    )


def _guess_priority(text: str) -> str:
    lowered = text.lower()  # 转小写
    if any(word in lowered for word in ["p0", "全站", "核心链路中断", "资损"]):  # P0 信号
        return "P0"
    if any(word in lowered for word in ["p1", "critical", "不可用", "失败率"]):  # P1 信号
        return "P1"
    if any(word in lowered for word in ["p3", "info", "恢复", "观察"]):  # P3 信号
        return "P3"
    return "P3"  # 默认 P3


def _risk_from_priority(priority: Severity) -> str:
    if priority == Severity.P0:  # P0 -> 高风险
        return "HIGH"
    if priority in {Severity.P1, Severity.P2}:  # P1/P2 -> 中风险
        return "MEDIUM"
    return "LOW"  # 其余低风险


def _guess_alert_type(text: str) -> str:
    lowered = text.lower()  # 转小写
    if any(word in lowered for word in ["发布", "变更", "deploy", "release"]):  # 变更类
        return "event"
    if any(word in lowered for word in ["cpu", "内存", "磁盘", "主机", "容器", "node"]):  # 主机类
        return "host"
    if any(word in lowered for word in ["订单", "支付", "核销率", "成功率", "业务"]):  # 业务类
        return "business"
    return "problem"  # 默认问题类


def status() -> dict:
    settings = get_settings()  # 读取配置
    return {
        "status": "READY",  # 运行时就绪
        "domain": "alerting",  # 领域
        "runtime": "event_driven_multi_agent",  # 运行时类型
        "maxSteps": settings.agent_runtime_max_steps,  # 最大轮数
        "maxClaimsPerRound": settings.agent_runtime_max_claims_per_round,  # 每轮最大认领数
        "maxClaimsPerAgent": settings.agent_runtime_max_claims_per_agent,  # 每 Agent 最大认领数
        "finalAcceptMinConfidence": settings.agent_final_accept_min_confidence,  # 采纳最小置信度
    }