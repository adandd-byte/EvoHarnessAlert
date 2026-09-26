from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class AiMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    """对话式排障请求：用户在会话里输入的告警问题或告警原文。"""

    message: str = Field(min_length=1)
    sessionId: Optional[str] = None


class ChatStreamEvent(BaseModel):
    """SSE 流式对话事件：meta（会话开始）/ token（增量内容）/ done（结束）。"""

    type: str
    sessionId: Optional[str] = None
    content: Optional[str] = None


class AlertWebhookRequest(BaseModel):
    source: str = Field(default="webhook", min_length=1)
    externalId: str = ""
    fingerprint: Optional[str] = None
    title: str = Field(min_length=1)
    description: str = ""
    labels: dict[str, Any] = Field(default_factory=dict)
    annotations: dict[str, Any] = Field(default_factory=dict)
    severity: str = "P2"
    alertType: str = "problem"
    status: str = "firing"
    startsAt: Optional[datetime] = None
    endsAt: Optional[datetime] = None


class AlertIngestResponse(BaseModel):
    alertId: int
    incidentId: Optional[int] = None
    severity: str
    alertType: str
    status: str
    fingerprint: str
    queuedJobs: list[str]
    message: str


class AckRequest(BaseModel):
    actor: str = "admin"
    note: str = ""


class ResolveRequest(BaseModel):
    actor: str = "admin"
    note: str = ""


class IncidentNoteRequest(BaseModel):
    actor: str = "admin"
    note: str = Field(min_length=1)


class KnowledgeIngestRequest(BaseModel):
    source: str
    content: str


class KnowledgeIngestResponse(BaseModel):
    source: str
    chunks: int


class AlertEventResponse(BaseModel):
    id: int
    source: str
    externalId: str
    fingerprint: str
    title: str
    description: str
    labels: dict[str, Any]
    annotations: dict[str, Any]
    severity: str
    alertType: str
    status: str
    startsAt: Optional[datetime] = None
    endsAt: Optional[datetime] = None
    createdAt: datetime


class IncidentResponse(BaseModel):
    id: int
    incidentKey: str
    severity: str
    alertType: str
    status: str
    owner: str
    summary: str
    impact: str
    rootCauseHint: str
    runbookHint: str
    alertCount: int
    lastAlertId: int
    acknowledgedBy: Optional[str] = None
    acknowledgedAt: Optional[datetime] = None
    resolvedBy: Optional[str] = None
    resolvedAt: Optional[datetime] = None
    createdAt: datetime
    updatedAt: datetime


class IncidentNoteResponse(BaseModel):
    id: int
    incidentId: int
    actor: str
    note: str
    createdAt: datetime


class ToolRecordResponse(BaseModel):
    id: int
    alertId: int
    incidentId: Optional[int] = None
    status: str
    message: str
    createdAt: datetime
    channel: Optional[str] = None
    recipient: Optional[str] = None
    filePath: Optional[str] = None


class ToolJobResponse(BaseModel):
    id: int
    alertId: int
    incidentId: Optional[int] = None
    kind: str
    status: str
    attempts: int
    maxAttempts: int
    runAfter: datetime
    lastError: str
    createdAt: datetime
    updatedAt: datetime


class DeadLetterResponse(BaseModel):
    id: int
    jobId: Optional[int] = None
    alertId: int
    incidentId: Optional[int] = None
    kind: str
    reason: str
    payload: str
    createdAt: datetime


class AgentRunTraceResponse(BaseModel):
    id: int
    alertId: int
    incidentId: Optional[int] = None
    severity: str
    alertType: str
    originalPayload: dict[str, Any]
    agentSteps: list[dict[str, Any]]
    retrievedKnowledge: list[dict[str, Any]]
    assessment: dict[str, Any]
    createdAt: datetime


class ToolAuditResponse(BaseModel):
    id: int
    jobId: Optional[int] = None
    alertId: Optional[int] = None
    toolName: str
    policy: str
    allowed: bool
    status: str
    reason: str
    payload: dict[str, Any]
    createdAt: datetime
    updatedAt: datetime


def authority(role: str) -> dict[str, Any]:
    return {"authority": role}
