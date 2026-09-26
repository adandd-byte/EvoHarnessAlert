"""Agent 执行轨迹服务：把一轮研判的完整过程落库到 AgentRunTrace。

参考 mindbridge-py 的 app/services/trace.py 移植，领域从"心理报告"改为"告警研判"。

保存的内容分三类：
- agent steps：runtime 记录的确定性步骤（归一化、调度、采纳）；
- collaboration events / tasks / artifacts：黑板协作全过程（谁认领了什么、
  发布了什么产物、置信度多少）——这是复盘和审计的核心；
- retrieved knowledge / assessment：RAG 检索证据与最终研判结论。

轨迹按 alert_id 关联告警；对话式排障（没有告警上下文）时 alert_id 记 0，
同样落库，保证"为什么这么回答"可回放。
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any

from sqlalchemy.orm import Session

from app.agents.result import AgentRunResult
from app.models.entities import AgentRunTrace, AlertEvent


class AgentTraceService:
    def __init__(self, db: Session):
        self.db = db

    def save_run(
        self,
        alert: AlertEvent | None,
        incident_id: int | None,
        agent_run: AgentRunResult,
        severity: str | None = None,
        alert_type: str | None = None,
    ) -> AgentRunTrace:
        """保存一轮 Agent 研判轨迹。

        - alert 给定时（webhook 主链路）：从告警实体取严重级别/类型/原始 payload；
        - alert 为 None 时（对话式排障）：用调用方传入的 severity/alert_type 兜底，
          alert_id 记 0 表示"无告警上下文的问答轮次"。
        """
        severity = severity or (alert.severity if alert is not None else "UNKNOWN")
        alert_type = alert_type or (alert.alert_type if alert is not None else "PROBLEM")
        trace = AgentRunTrace(
            alert_id=alert.id if alert is not None else 0,
            incident_id=incident_id,
            severity=severity,
            alert_type=alert_type,
            original_payload_json=_json(alert.raw_payload_json if alert is not None else {}),
            agent_steps_json=_json(_agent_steps_with_collaboration(agent_run)),
            retrieved_knowledge_json=_json(agent_run.retrieved_knowledge),
            assessment_json=_json(agent_run.assessment or {}),
        )
        self.db.add(trace)
        self.db.commit()
        self.db.refresh(trace)
        return trace


def _json(value: Any) -> str:
    return json.dumps(_to_jsonable(value), ensure_ascii=False, default=str)


def _agent_steps_with_collaboration(agent_run: AgentRunResult) -> list[Any]:
    """把确定性步骤与黑板协作记录合并成一个时间线，便于前端/复盘顺序阅读。"""
    entries: list[Any] = [{"kind": "agent_step", **_to_jsonable(step)} for step in agent_run.steps]
    entries.extend(
        {
            "kind": "agent_event",
            "type": getattr(event.type, "value", event.type),
            "actor": event.actor,
            "taskId": event.task_id,
            "artifactId": event.artifact_id,
            "message": event.message,
            "metadata": event.metadata,
        }
        for event in agent_run.collaboration_events
    )
    entries.extend(
        {
            "kind": "agent_task",
            "id": task.id,
            "title": task.title,
            "status": getattr(task.status, "value", task.status),
            "priority": getattr(task.priority, "value", task.priority),
            "requiredCapabilities": sorted(task.required_capabilities),
            "claimedBy": list(task.claimed_by),
            "createdBy": task.created_by,
            "metadata": task.metadata,
        }
        for task in agent_run.collaboration_tasks
    )
    entries.extend(
        {
            "kind": "agent_artifact",
            "id": artifact.id,
            "owner": artifact.owner,
            "artifactKind": artifact.kind,
            "confidence": artifact.confidence,
            "taskId": artifact.task_id,
            "metadata": artifact.metadata,
            "payload": artifact.payload,
        }
        for artifact in agent_run.collaboration_artifacts
    )
    return entries


def _to_jsonable(value: Any) -> Any:
    """递归把 dataclass / Enum / pydantic 模型转成可 JSON 序列化的结构。"""
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return _to_jsonable(asdict(value))
    if hasattr(value, "model_dump"):
        return _to_jsonable(value.model_dump())
    if isinstance(value, list):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    return value
