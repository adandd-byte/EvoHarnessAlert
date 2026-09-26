"""告警对话式排障入口（Agent Harness）。

参考 mindbridge-py 的 app/agents/harness.py 移植。

职责边界：
- HTTP/SSE 层保持很薄：只负责鉴权、参数校验和流式输出；
- 本模块承接"一轮对话"的业务编排：输入脱敏 -> 会话解析 -> Agent 运行时
  执行 -> 会话记忆更新 -> 执行轨迹落库 -> 返回结构化结果。

与 webhook 主链路（app/services/alerting.py）的关系：
- webhook 链路面向机器：告警源 POST 过来，围绕 AlertEvent 建 Incident、
  入队工具任务，trace 挂在告警上；
- 本模块面向人：值班人员在对话里贴告警原文或追问根因，没有 AlertEvent
  实体，因此 trace 的 alert_id 记 0（"无告警上下文轮次"），会话历史只存
  Redis（本项目没有 ChatSession 表，对话轻量、可随时重建）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.agents.factory import create_agent_runtime
from app.agents.result import AgentStep
from app.core.config import Settings
from app.models.entities import UserAccount
from app.schemas.dtos import AiMessage, ChatRequest
from app.services.knowledge import SearchResult
from app.services.memory import RedisShortTermMemoryStore
from app.services.privacy import PrivacySanitizer
from app.services.trace import AgentTraceService


@dataclass
class AlertAgentOutcome:
    """一轮对话式排障的结构化结果：HTTP 层直接把它转成响应/流事件。"""

    session_id: str                       # Redis 会话标识（客户端下次传入可继续会话）
    original_input: str                   # 用户原始输入（回显用）
    model_input: str                      # 脱敏后的输入（真正进模型的内容）
    intent: str                           # ALERT / CHAT
    severity: str                         # 规则归一的告警级别（P0-P3）
    assessment: dict | None               # 最终研判结论（summary/impact/...）
    response_messages: list[AiMessage]    # 面向用户的回复消息列表
    agent_steps: list[AgentStep]          # 确定性执行步骤
    retrieved_knowledge: list[SearchResult]  # RAG 检索证据
    trace_id: int | None                  # 执行轨迹 ID（可回放）


class AlertAgentHarness:
    """单轮告警对话的运行时 harness。"""

    def __init__(self, db: Session, settings: Settings):
        self.db = db
        self.settings = settings
        self.privacy = PrivacySanitizer()
        self.memory = RedisShortTermMemoryStore(settings)

    def run(self, user: UserAccount, request: ChatRequest) -> AlertAgentOutcome:
        """执行一轮对话：脱敏 -> 运行时 -> 记忆 -> 轨迹 -> 结果。"""
        original_input = request.message.strip()
        model_input = self.privacy.sanitize(original_input)
        session_id = self._resolve_session_id(request.sessionId)

        # 1) 运行时执行（黑板协作 + 确定性归一都在 runtime 内部完成）
        agent_run = create_agent_runtime(self.settings).run(model_input, session_id=session_id)

        # 2) 会话记忆：原始输入进 user 角色，回复要点进 assistant 角色
        self._remember(user, session_id, "user", original_input)

        # 3) 执行轨迹落库（对话场景无 AlertEvent，alert_id=0）
        trace = AgentTraceService(self.db).save_run(
            alert=None,
            incident_id=None,
            agent_run=agent_run,
            severity=str(getattr(agent_run.priority, "value", agent_run.priority)),
            alert_type=str(getattr(agent_run.alert_type, "value", agent_run.alert_type)),
        )

        return AlertAgentOutcome(
            session_id=session_id,
            original_input=original_input,
            model_input=model_input,
            intent=agent_run.intent,
            severity=str(getattr(agent_run.priority, "value", agent_run.priority)),
            assessment=agent_run.assessment,
            response_messages=agent_run.response_messages,
            agent_steps=agent_run.steps,
            retrieved_knowledge=agent_run.retrieved_knowledge,
            trace_id=trace.id,
        )

    def save_assistant_message(self, user: UserAccount, session_id: str, content: str) -> None:
        """流式输出结束后，把完整的助手回复写入会话记忆。"""
        self._remember(user, session_id, "assistant", content)

    def save_message(self, user: UserAccount, session_id: str, role: str, content: str) -> None:
        """通用消息落库：写 Redis 会话记忆（本项目无对话表，Redis 即会话源）。"""
        self._remember(user, session_id, role, content)

    # ---------------------------------------------------------------- 内部工具

    def _resolve_session_id(self, public_id: str | None) -> str:
        """复用客户端带来的会话 ID，或新建一个；本项目会话只存 Redis。"""
        return (public_id or "").strip() or uuid.uuid4().hex

    def _remember(self, user: UserAccount, session_id: str, role: str, content: str) -> None:
        # owner 用用户名做隔离：不同值班人员即使 sessionId 相同也不串会话
        self.memory.append(user.username, session_id, role, content)
