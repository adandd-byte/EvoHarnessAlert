"""工具治理：在工具真正执行前做策略授权，并留下可审计的记录。

参考 mindbridge-py 的 app/services/tool_governance.py 移植，领域从
"心理报告驱动" 改为 "告警严重级别驱动"。

为什么需要治理层？
- 工具（写台账、建 Incident、发通知）都有副作用，不能谁拿到任务都能执行；
- 例如：值班通知只允许 P0/P1 触发——P3 也发通知就是骚扰，属于策略违规；
- 每次授权（无论放行还是拦截）都落一条 ToolAuditRecord，出问题可追溯：
  谁的任务、什么策略、为什么放行/拦截、最终状态。

与 mindbridge 的差异：
- 授权依据不是 PsychologicalReport.risk_level，而是告警 severity（P0-P3）；
- ToolAuditRecord 的 report_id 字段在本项目换成 alert_id。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.enums import Severity, ToolJobKind
from app.models.entities import ToolAuditRecord, ToolJob


@dataclass(frozen=True)
class ToolPolicy:
    """单个工具的执行策略：允许在什么严重级别下触发。"""

    name: str
    description: str
    allowed_severities: tuple[str, ...]


class ToolPolicyRegistry:
    """内置策略表：键是工具类型（ToolJobKind），值是该工具的放行规则。"""

    POLICIES: dict[str, ToolPolicy] = {
        # 台账写入：任何告警都要登记，全级别放行
        ToolJobKind.LEDGER_WRITE.value: ToolPolicy(
            name=ToolJobKind.LEDGER_WRITE.value,
            description="把告警处置记录追加写入告警台账 Excel，任何级别都登记。",
            allowed_severities=(Severity.P0.value, Severity.P1.value, Severity.P2.value, Severity.P3.value),
        ),
        # Incident 更新：P3 不建 Incident，因此 P3 不允许执行
        ToolJobKind.INCIDENT_UPSERT.value: ToolPolicy(
            name=ToolJobKind.INCIDENT_UPSERT.value,
            description="创建或更新事件（Incident）；P3 告警不建 Incident。",
            allowed_severities=(Severity.P0.value, Severity.P1.value, Severity.P2.value),
        ),
        # 值班通知：只有 P0/P1 才值得打扰值班，防止告警疲劳
        ToolJobKind.NOTIFICATION_SEND.value: ToolPolicy(
            name=ToolJobKind.NOTIFICATION_SEND.value,
            description="向值班渠道发送告警通知；仅 P0/P1 允许，避免低级别告警骚扰。",
            allowed_severities=(Severity.P0.value, Severity.P1.value),
        ),
    }

    @classmethod
    def policy_for(cls, tool_name: str) -> ToolPolicy | None:
        return cls.POLICIES.get(tool_name)

    @classmethod
    def authorize(cls, tool_name: str, severity: str | None) -> tuple[bool, str, ToolPolicy | None]:
        """授权判定：返回 (是否放行, 原因, 命中的策略)。"""
        policy = cls.policy_for(tool_name)
        if policy is None:
            return False, f"未知工具：{tool_name}", None
        if severity not in policy.allowed_severities:
            return False, f"工具 {tool_name} 不允许在严重级别 {severity} 下执行", policy
        return True, "允许执行", policy


class ToolGovernanceService:
    """工具治理服务：任务执行前授权、执行后回写审计结果。"""

    def __init__(self, db: Session):
        self.db = db

    def start_job(self, job: ToolJob, severity: str | None) -> ToolAuditRecord:
        """任务开始前做授权并落审计记录（AUTHORIZED 或 BLOCKED）。"""
        allowed, reason, policy = ToolPolicyRegistry.authorize(job.kind, severity)
        record = ToolAuditRecord(
            job_id=job.id,
            alert_id=job.alert_id,
            tool_name=job.kind,
            policy=policy.name if policy else "unknown",
            allowed=allowed,
            status="AUTHORIZED" if allowed else "BLOCKED",
            reason=reason,
            payload=_json(
                {
                    "jobId": job.id,
                    "kind": job.kind,
                    "attempts": job.attempts,
                    "severity": severity,
                    "policy": asdict(policy) if policy else None,
                }
            ),
        )
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        return record

    def require_allowed(self, job: ToolJob, severity: str | None) -> None:
        """强约束版本：不允许执行直接抛异常，由 worker 走失败/死信流程。"""
        allowed, reason, _ = ToolPolicyRegistry.authorize(job.kind, severity)
        if not allowed:
            raise RuntimeError(reason)

    def finish(self, record: ToolAuditRecord, status: str, reason: str = "", payload: dict[str, Any] | None = None) -> ToolAuditRecord:
        """任务结束后回写审计状态（SUCCESS/FAILED 等）。"""
        record.status = status
        record.reason = reason or record.reason
        if payload is not None:
            record.payload = _json(payload)
        record.updated_at = datetime.utcnow()
        self.db.add(record)
        self.db.commit()
        return record


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)
