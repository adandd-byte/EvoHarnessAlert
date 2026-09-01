from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.enums import AlertType, Severity
from app.schemas.dtos import AiMessage
from app.services.knowledge import SearchResult


@dataclass
class AgentStep:
    step: int
    agent: str
    action: str
    observation: str


@dataclass
class AgentRunResult:
    intent: str
    priority: Severity | str
    alert_type: AlertType | str
    risk_level: str
    assessment: dict[str, Any] | None
    retrieved_knowledge: list[SearchResult]
    response_messages: list[AiMessage]
    steps: list[AgentStep]
    memory_brief: str
    collaboration_events: list[Any] = field(default_factory=list)
    collaboration_tasks: list[Any] = field(default_factory=list)
    collaboration_artifacts: list[Any] = field(default_factory=list)

    @property
    def requires_report(self) -> bool:
        return self.intent != "CHAT"
