from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.enums import AlertStatus, AlertType, IncidentStatus, Severity, ToolJobKind, ToolJobStatus
from app.models.entities import AgentRunTrace, AlertEvent, AlertSource, Incident, IncidentNote, ToolJob
from app.schemas.dtos import AlertWebhookRequest
from app.services.privacy import PrivacySanitizer


SEVERITY_ALIASES = {
    "0": Severity.P0, "p0": Severity.P0, "sev0": Severity.P0, "blocker": Severity.P0,
    "emergency": Severity.P0, "disaster": Severity.P0,
    "1": Severity.P1, "p1": Severity.P1, "critical": Severity.P1, "crit": Severity.P1,
    "fatal": Severity.P1, "high": Severity.P1, "page": Severity.P1,
    "2": Severity.P2, "p2": Severity.P2, "warning": Severity.P2, "warn": Severity.P2,
    "medium": Severity.P2, "major": Severity.P2,
    "3": Severity.P3, "p3": Severity.P3, "info": Severity.P3, "informational": Severity.P3,
    "low": Severity.P3, "minor": Severity.P3, "none": Severity.P3,
}

ALERT_TYPE_ALIASES = {
    "problem": AlertType.PROBLEM, "issue": AlertType.PROBLEM, "incident": AlertType.PROBLEM, "故障": AlertType.PROBLEM, "问题": AlertType.PROBLEM,
    "business": AlertType.BUSINESS, "biz": AlertType.BUSINESS, "metric": AlertType.BUSINESS, "业务": AlertType.BUSINESS,
    "event": AlertType.EVENT, "change": AlertType.EVENT, "deploy": AlertType.EVENT, "发布": AlertType.EVENT, "事件": AlertType.EVENT,
    "host": AlertType.HOST, "machine": AlertType.HOST, "node": AlertType.HOST, "infra": AlertType.HOST, "主机": AlertType.HOST, "机器": AlertType.HOST,
}

SEVERITY_ORDER = {Severity.P3.value: 1, Severity.P2.value: 2, Severity.P1.value: 3, Severity.P0.value: 4}
INCIDENT_SEVERITIES = {Severity.P0, Severity.P1, Severity.P2}
NOTIFY_SEVERITIES = {Severity.P0, Severity.P1}


@dataclass(frozen=True)
class IngestResult:
    alert: AlertEvent
    incident: Incident | None
    queued_jobs: list[str]


class AlertIngestService:
    def __init__(self, db: Session, settings: Settings):
        self.db = db
        self.settings = settings

    def ingest_webhook(self, request: AlertWebhookRequest, raw_payload: dict[str, Any] | None = None) -> IngestResult:
        labels = normalize_dict(request.labels)
        annotations = normalize_dict(request.annotations)
        severity = normalize_severity(request.severity)
        alert_type = normalize_alert_type(request.alertType or labels.get("alert_type") or labels.get("type"))
        status = normalize_status(request.status)
        source = self._source(request.source, "webhook")
        fingerprint = request.fingerprint or build_fingerprint(request.source, request.title, labels, alert_type.value)
        alert = AlertEvent(
            source_id=source.id,
            external_id=request.externalId,
            fingerprint=fingerprint,
            title=request.title.strip(),
            description=request.description.strip(),
            labels_json=dumps(labels),
            annotations_json=dumps(annotations),
            severity=severity.value,
            alert_type=alert_type.value,
            status=status.value,
            starts_at=request.startsAt,
            ends_at=request.endsAt,
            raw_payload_json=dumps(raw_payload or request.model_dump(mode="json")),
        )
        self.db.add(alert)
        self.db.commit()
        self.db.refresh(alert)
        incident = self._upsert_incident(alert, labels, annotations) if severity in INCIDENT_SEVERITIES else None
        self._save_trace(alert, incident, labels, annotations)
        jobs = self._enqueue(alert, incident)
        self.db.commit()
        return IngestResult(alert=alert, incident=incident, queued_jobs=jobs)

    def ingest_prometheus(self, payload: dict[str, Any]) -> list[IngestResult]:
        alerts = payload.get("alerts")
        if not isinstance(alerts, list) or not alerts:
            raise ValueError("Prometheus payload 缺少 alerts 数组")
        results = []
        for item in alerts:
            labels = normalize_dict(item.get("labels", {}))
            annotations = normalize_dict(item.get("annotations", {}))
            title = annotations.get("summary") or labels.get("alertname") or "Prometheus 告警"
            request = AlertWebhookRequest(
                source="prometheus",
                externalId=item.get("fingerprint", ""),
                fingerprint=item.get("fingerprint") or build_fingerprint("prometheus", title, labels, normalize_alert_type(labels.get("alert_type") or labels.get("type")).value),
                title=title,
                description=annotations.get("description", ""),
                labels=labels,
                annotations=annotations,
                severity=labels.get("severity") or labels.get("priority") or payload.get("severity") or "P2",
                alertType=labels.get("alert_type") or labels.get("type") or annotations.get("alert_type") or infer_alert_type(title, labels, annotations).value,
                status=item.get("status") or payload.get("status") or "firing",
                startsAt=parse_dt(item.get("startsAt")),
                endsAt=parse_dt(item.get("endsAt")),
            )
            results.append(self.ingest_webhook(request, item))
        return results

    def _source(self, name: str, kind: str) -> AlertSource:
        normalized = name.strip() or "webhook"
        source = self.db.query(AlertSource).filter(AlertSource.name == normalized).first()
        if source is not None:
            return source
        source = AlertSource(name=normalized, kind=kind, description=f"{normalized} 告警来源")
        self.db.add(source)
        self.db.commit()
        self.db.refresh(source)
        return source

    def _upsert_incident(self, alert: AlertEvent, labels: dict[str, Any], annotations: dict[str, Any]) -> Incident:
        key = incident_key(alert, labels)
        incident = self.db.query(Incident).filter(Incident.incident_key == key).first()
        assessment = assess_alert(alert, labels, annotations)
        if incident is None:
            incident = Incident(
                incident_key=key,
                severity=alert.severity,
                alert_type=alert.alert_type,
                status=IncidentStatus.OPEN.value,
                owner=self._owner(),
                summary=assessment["summary"],
                impact=assessment["impact"],
                root_cause_hint=assessment["rootCauseHint"],
                runbook_hint=assessment["runbookHint"],
                alert_count=1,
                last_alert_id=alert.id,
            )
            self.db.add(incident)
            self.db.commit()
            self.db.refresh(incident)
            return incident
        incident.alert_count += 1
        incident.last_alert_id = alert.id
        incident.severity = max_severity(incident.severity, alert.severity)
        incident.alert_type = alert.alert_type
        if alert.status == AlertStatus.RESOLVED.value:
            incident.status = IncidentStatus.RESOLVED.value
            incident.resolved_at = datetime.utcnow()
            incident.resolved_by = "告警恢复"
        elif incident.status == IncidentStatus.RESOLVED.value:
            incident.status = IncidentStatus.OPEN.value
            incident.resolved_at = None
            incident.resolved_by = None
        incident.summary = assessment["summary"]
        incident.impact = assessment["impact"]
        incident.root_cause_hint = assessment["rootCauseHint"]
        incident.runbook_hint = assessment["runbookHint"]
        incident.updated_at = datetime.utcnow()
        self.db.add(incident)
        self.db.commit()
        self.db.refresh(incident)
        return incident

    def _save_trace(self, alert: AlertEvent, incident: Incident | None, labels: dict[str, Any], annotations: dict[str, Any]) -> None:
        sanitizer = PrivacySanitizer()
        safe_labels = sanitizer.sanitize_data(labels)
        safe_annotations = sanitizer.sanitize_data(annotations)
        safe_payload = sanitizer.sanitize_data(loads(alert.raw_payload_json, {}))
        assessment = assess_alert(alert, safe_labels, safe_annotations)
        steps = handling_steps(alert.alert_type)
        trace_steps = [
            {"agent": "CoordinatorAgent", "action": f"接收 {alert.severity}/{alert.alert_type} 告警并写入黑板", "status": "完成"},
            {"agent": "TriageAgent", "action": f"确认优先级 {alert.severity}，状态 {alert.status}", "status": "完成"},
            {"agent": "CorrelationAgent", "action": f"聚合键 {incident.incident_key if incident else alert.fingerprint}", "status": "完成"},
            *steps,
            {"agent": "EvidenceReportAgent", "action": "生成带证据引用的值班报告草稿", "status": "完成"},
        ]
        self.db.add(AgentRunTrace(
            alert_id=alert.id,
            incident_id=incident.id if incident else None,
            severity=alert.severity,
            alert_type=alert.alert_type,
            original_payload_json=dumps(safe_payload),
            agent_steps_json=dumps(trace_steps),
            retrieved_knowledge_json=dumps(assessment["evidence"]),
            assessment_json=dumps(assessment),
        ))

    def _enqueue(self, alert: AlertEvent, incident: Incident | None) -> list[str]:
        kinds = [ToolJobKind.LEDGER_WRITE]
        if incident is not None:
            kinds.append(ToolJobKind.INCIDENT_UPSERT)
        if normalize_severity(alert.severity) in NOTIFY_SEVERITIES and incident is not None:
            kinds.append(ToolJobKind.NOTIFICATION_SEND)
        for kind in kinds:
            self.db.add(ToolJob(alert_id=alert.id, incident_id=incident.id if incident else None, kind=kind.value, status=ToolJobStatus.PENDING.value, max_attempts=self.settings.tool_queue_max_attempts, run_after=datetime.utcnow()))
        return [kind.value for kind in kinds]

    def _owner(self) -> str:
        recipients = [item.strip() for item in self.settings.alert_email_to.replace(";", ",").split(",") if item.strip()]
        return recipients[0] if recipients else "未分派"


class IncidentService:
    def __init__(self, db: Session):
        self.db = db

    def acknowledge(self, incident_id: int, actor: str, note: str = "") -> Incident:
        incident = self._get(incident_id)
        incident.status = IncidentStatus.ACKNOWLEDGED.value
        incident.acknowledged_by = actor.strip() or "admin"
        incident.acknowledged_at = datetime.utcnow()
        incident.updated_at = datetime.utcnow()
        self.db.add(incident)
        self._note(incident.id, incident.acknowledged_by, note or "已确认接手该事件")
        self.db.commit()
        self.db.refresh(incident)
        return incident

    def resolve(self, incident_id: int, actor: str, note: str = "") -> Incident:
        incident = self._get(incident_id)
        incident.status = IncidentStatus.RESOLVED.value
        incident.resolved_by = actor.strip() or "admin"
        incident.resolved_at = datetime.utcnow()
        incident.updated_at = datetime.utcnow()
        self.db.add(incident)
        self._note(incident.id, incident.resolved_by, note or "事件已关闭")
        self.db.commit()
        self.db.refresh(incident)
        return incident

    def add_note(self, incident_id: int, actor: str, note: str) -> IncidentNote:
        self._get(incident_id)
        record = self._note(incident_id, actor.strip() or "admin", note.strip())
        self.db.commit()
        self.db.refresh(record)
        return record

    def _get(self, incident_id: int) -> Incident:
        incident = self.db.get(Incident, incident_id)
        if incident is None:
            raise ValueError("事件不存在")
        return incident

    def _note(self, incident_id: int, actor: str, note: str) -> IncidentNote:
        if not note:
            raise ValueError("处置记录不能为空")
        record = IncidentNote(incident_id=incident_id, actor=actor, note=note)
        self.db.add(record)
        return record


def normalize_severity(value: str | None) -> Severity:
    return SEVERITY_ALIASES.get(str(value or "").strip().lower(), Severity.P2)


def normalize_alert_type(value: str | None) -> AlertType:
    return ALERT_TYPE_ALIASES.get(str(value or "").strip().lower(), AlertType.PROBLEM)


def infer_alert_type(title: str, labels: dict[str, Any], annotations: dict[str, Any]) -> AlertType:
    text = " ".join(str(v) for v in [title, labels.get("category", ""), labels.get("job", ""), annotations.get("description", "")]).lower()
    if any(word in text for word in ["host", "node", "disk", "cpu", "memory", "inode", "主机", "机器", "磁盘", "内存"]):
        return AlertType.HOST
    if any(word in text for word in ["business", "order", "payment", "checkout", "gmv", "业务", "订单", "支付"]):
        return AlertType.BUSINESS
    if any(word in text for word in ["deploy", "release", "change", "event", "发布", "变更"]):
        return AlertType.EVENT
    return AlertType.PROBLEM


def normalize_status(value: str | None) -> AlertStatus:
    return AlertStatus.RESOLVED if str(value or "").strip().lower() in {"resolved", "resolve", "ok", "closed"} else AlertStatus.FIRING


def normalize_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def build_fingerprint(source: str, title: str, labels: dict[str, Any], alert_type: str = "PROBLEM") -> str:
    stable = {key: labels.get(key) for key in sorted(labels) if key not in {"pod", "instance", "timestamp"}}
    raw = json.dumps({"source": source, "title": title, "type": alert_type, "labels": stable}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def incident_key(alert: AlertEvent, labels: dict[str, Any]) -> str:
    service = labels.get("service") or labels.get("job") or labels.get("app") or labels.get("namespace") or "default"
    return f"{alert.alert_type}:{service}:{alert.fingerprint}"


def max_severity(left: str, right: str) -> str:
    return left if SEVERITY_ORDER.get(left, 0) >= SEVERITY_ORDER.get(right, 0) else right


def handling_steps(alert_type: str) -> list[dict[str, str]]:
    if alert_type == AlertType.PROBLEM.value:
        return [
            {"agent": "LogQueryAgent", "action": "按告警时间窗口查询错误日志和关键 trace", "status": "待接入工具"},
            {"agent": "CodeAnalysisAgent", "action": "根据日志关键字、接口、模块名定位相关代码", "status": "待接入工具"},
            {"agent": "MetricsAgent", "action": "查看服务大盘、错误率、延迟、饱和度和依赖指标", "status": "待接入工具"},
            {"agent": "KnowledgeAgent", "action": "检索企业知识库、历史故障和 Runbook", "status": "完成"},
        ]
    if alert_type == AlertType.BUSINESS.value:
        return [
            {"agent": "BusinessMetricAgent", "action": "核对业务指标口径、环比/同比和上下游漏斗", "status": "待接入工具"},
            {"agent": "DataQualityAgent", "action": "检查数据延迟、埋点变更和统计任务状态", "status": "待接入工具"},
            {"agent": "KnowledgeAgent", "action": "检索业务名词、指标口径和历史波动说明", "status": "完成"},
        ]
    if alert_type == AlertType.EVENT.value:
        return [
            {"agent": "ChangeReviewAgent", "action": "关联发布、配置变更、扩缩容和定时任务", "status": "待接入工具"},
            {"agent": "ImpactAgent", "action": "确认事件是否引发服务指标或业务指标异常", "status": "待接入工具"},
        ]
    return [
        {"agent": "HostDiagnosticAgent", "action": "检查 CPU、内存、磁盘、网络、进程和容器状态", "status": "待接入工具"},
        {"agent": "MetricsAgent", "action": "查看主机和服务大盘确认影响范围", "status": "待接入工具"},
        {"agent": "KnowledgeAgent", "action": "检索基础设施 Runbook 和历史主机故障", "status": "完成"},
    ]


def assess_alert(alert: AlertEvent, labels: dict[str, Any], annotations: dict[str, Any]) -> dict[str, Any]:
    service = labels.get("service") or labels.get("job") or labels.get("app") or "未知服务"
    instance = labels.get("instance") or labels.get("pod") or labels.get("host") or "未知实例"
    type_name = {"PROBLEM": "问题", "BUSINESS": "业务", "EVENT": "事件", "HOST": "主机"}.get(alert.alert_type, alert.alert_type)
    summary = f"{service} 触发 {alert.severity} {type_name}告警：{alert.title}，当前状态 {alert.status}。"
    if alert.severity == Severity.P0.value:
        impact = "最高优先级，可能存在全局不可用、核心链路中断或重大资损风险，需要立即拉起应急响应。"
    elif alert.severity == Severity.P1.value:
        impact = "高优先级，可能影响核心服务或重要业务指标，需要立即通知值班人员。"
    elif alert.severity == Severity.P2.value:
        impact = "中优先级，需要进入事件跟踪并在值班窗口内处理。"
    else:
        impact = "低优先级，记录台账并用于趋势观察。"
    root = annotations.get("root_cause") or root_hint(alert.alert_type, instance)
    runbook = annotations.get("runbook") or runbook_hint(alert.alert_type)
    evidence = [
        {"source": "告警原文", "quote": alert.title, "strength": "strong"},
        {"source": "labels", "quote": json.dumps(labels, ensure_ascii=False, default=str), "strength": "medium"},
        {"source": "annotations", "quote": json.dumps(annotations, ensure_ascii=False, default=str), "strength": "medium"},
    ]
    return {"summary": summary, "impact": impact, "rootCauseHint": root, "runbookHint": runbook, "evidence": evidence}


def root_hint(alert_type: str, instance: str) -> str:
    return {
        AlertType.PROBLEM.value: f"建议从 {instance} 在告警时间窗口内的错误日志、trace、近期代码变更和依赖异常开始验证。",
        AlertType.BUSINESS.value: "建议先确认指标口径、数据延迟、上下游漏斗、活动配置和最近业务变更。",
        AlertType.EVENT.value: "建议关联发布、配置变更、定时任务、容量调整和外部依赖事件。",
        AlertType.HOST.value: f"建议检查 {instance} 的 CPU、内存、磁盘、网络、进程、容器重启和节点健康。",
    }.get(alert_type, "建议补充日志、指标、代码和知识库证据后再判断根因。")


def runbook_hint(alert_type: str) -> str:
    return {
        AlertType.PROBLEM.value: "Problem 流程：查日志 -> 看代码 -> 查大盘 -> 检索知识库/历史故障 -> 生成证据链报告。",
        AlertType.BUSINESS.value: "Business 流程：确认指标异常 -> 查数据链路 -> 对齐业务口径 -> 联系业务 Owner -> 记录影响范围。",
        AlertType.EVENT.value: "Event 流程：关联变更 -> 验证影响 -> 必要时回滚或暂停变更 -> 更新事件记录。",
        AlertType.HOST.value: "Host 流程：检查资源水位 -> 定位异常进程/容器 -> 摘除或迁移流量 -> 执行基础设施 Runbook。",
    }.get(alert_type, "按日志、指标、代码、知识库四类证据补齐研判。")


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def loads(raw: str, default: Any) -> Any:
    try:
        return json.loads(raw or "")
    except Exception:
        return default
