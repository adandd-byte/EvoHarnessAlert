"""对话式排障服务：SSE 流式输出一轮告警问答。

参考 mindbridge-py 的 app/services/chat.py 移植。

流程：
1. AlertAgentHarness.run() 完成业务编排（脱敏/运行时/记忆/轨迹）；
2. 先给客户端发 meta 事件（带回 sessionId，客户端后续轮次要带上）；
3. 用模型流式接口逐 token 输出回复；
4. 输出完毕把完整回复写回会话记忆，发 done 事件。

事件格式（SSE）：
    event: meta\ndata: {"type":"meta","sessionId":"..."}\n\n
    event: token\ndata: {"type":"token","content":"..."}\n\n
    event: done\ndata: {"type":"done"}\n\n
"""

from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session

from app.agents.harness import AlertAgentHarness
from app.core.config import Settings
from app.models.entities import UserAccount
from app.schemas.dtos import ChatRequest, ChatStreamEvent
from app.services.ai import AiClient

logger = logging.getLogger(__name__)


class ChatService:
    def __init__(self, db: Session, settings: Settings):
        self.db = db
        self.settings = settings
        self.ai = AiClient(settings)
        self.agent_harness = AlertAgentHarness(db, settings)

    async def stream_chat(self, user: UserAccount, request: ChatRequest):
        """流式对话主循环；任何一步失败都不应中断 SSE，而是记录后发 done。"""
        # 1) 业务编排：脱敏、运行时研判、记忆、轨迹
        outcome = self.agent_harness.run(user, request)

        # 2) meta 事件：告知客户端会话 ID（多轮对话要回传）
        yield sse("meta", ChatStreamEvent(type="meta", sessionId=outcome.session_id).model_dump())

        # 3) 流式输出回复内容
        assistant: list[str] = []
        try:
            async for token in self.ai.stream(outcome.response_messages):
                assistant.append(token)
                yield sse("token", ChatStreamEvent(type="token", sessionId=outcome.session_id, content=token).model_dump())
        except Exception as exc:  # 流式失败：记录日志，客户端仍能收到 done 正常收尾
            logger.warning("对话流式输出失败 session=%s: %s", outcome.session_id, exc, exc_info=True)

        # 4) 完整回复写回会话记忆（空回复不写，避免污染历史）
        if assistant:
            self.agent_harness.save_assistant_message(user, outcome.session_id, "".join(assistant))

        yield sse("done", ChatStreamEvent(type="done", sessionId=outcome.session_id).model_dump())


def sse(event: str, data: dict) -> str:
    """格式化一条 SSE 事件；ensure_ascii=False 保证中文原样输出。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"
