from __future__ import annotations

from typing import Any  # 任意类型

from app.agents.event_driven_runtime import EventDrivenAgentRuntimeService
from app.core.config import Settings


def create_agent_runtime(db: Any = None, settings: Settings | None = None) -> EventDrivenAgentRuntimeService:
    return EventDrivenAgentRuntimeService(db=db, settings=settings)  # 工厂：统一入口创建运行时


def agent_framework_status(settings: Settings) -> dict:
    return {
        "requested": getattr(settings, "agent_framework", "event_driven_multi_agent"),  # 配置请求的框架
        "active": "event_driven_multi_agent",   # 实际激活的框架
        "available": ["event_driven_multi_agent"],  # 可用的框架列表
        "fallback": False,                      # 是否发生了降级
    }