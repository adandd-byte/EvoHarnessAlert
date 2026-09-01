from __future__ import annotations

from typing import Any

from app.agents.event_driven_runtime import EventDrivenAgentRuntimeService
from app.core.config import Settings


def create_agent_runtime(db: Any = None, settings: Settings | None = None) -> EventDrivenAgentRuntimeService:
    return EventDrivenAgentRuntimeService(db=db, settings=settings)


def agent_framework_status(settings: Settings) -> dict:
    return {
        "requested": getattr(settings, "agent_framework", "event_driven_multi_agent"),
        "active": "event_driven_multi_agent",
        "available": ["event_driven_multi_agent"],
        "fallback": False,
    }
