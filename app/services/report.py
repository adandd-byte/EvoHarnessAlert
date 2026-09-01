from __future__ import annotations

from app.models.entities import AgentRunTrace, AlertEvent, DeadLetterRecord, Incident, IncidentNote, LedgerRecord, NotificationRecord, ToolAuditRecord, ToolJob
from app.schemas.dtos import AgentRunTraceResponse, AlertEventResponse, DeadLetterResponse, IncidentNoteResponse, IncidentResponse, ToolAuditResponse, ToolJobResponse, ToolRecordResponse
from app.services.alerting import loads


class ReportService:
    def __init__(self, db):
        self.db = db

    def alerts(self) -> list[AlertEventResponse]:
        rows = self.db.query(AlertEvent).order_by(AlertEvent.created_at.desc()).limit(200).all()
        return [self._alert(row) for row in rows]

    def incidents(self) -> list[IncidentResponse]:
        rows = self.db.query(Incident).order_by(Incident.updated_at.desc()).limit(200).all()
        return [self._incident(row) for row in rows]

    def incident_notes(self, incident_id: int) -> list[IncidentNoteResponse]:
        rows = self.db.query(IncidentNote).filter(IncidentNote.incident_id == incident_id).order_by(IncidentNote.created_at.asc()).all()
        return [IncidentNoteResponse(id=row.id, incidentId=row.incident_id, actor=row.actor, note=row.note, createdAt=row.created_at) for row in rows]

    def ledger_records(self) -> list[ToolRecordResponse]:
        rows = self.db.query(LedgerRecord).order_by(LedgerRecord.created_at.desc()).limit(100).all()
        return [ToolRecordResponse(id=row.id, alertId=row.alert_id, status=row.status, message=row.message, createdAt=row.created_at, filePath=row.file_path) for row in rows]

    def notification_records(self) -> list[ToolRecordResponse]:
        rows = self.db.query(NotificationRecord).order_by(NotificationRecord.created_at.desc()).limit(100).all()
        return [ToolRecordResponse(id=row.id, alertId=row.alert_id, incidentId=row.incident_id, status=row.status, message=row.message, createdAt=row.created_at, channel=row.channel, recipient=row.recipient) for row in rows]

    def tool_jobs(self) -> list[ToolJobResponse]:
        rows = self.db.query(ToolJob).order_by(ToolJob.created_at.desc()).limit(100).all()
        return [ToolJobResponse(id=row.id, alertId=row.alert_id, incidentId=row.incident_id, kind=row.kind, status=row.status, attempts=row.attempts, maxAttempts=row.max_attempts, runAfter=row.run_after, lastError=row.last_error, createdAt=row.created_at, updatedAt=row.updated_at) for row in rows]

    def dead_letters(self) -> list[DeadLetterResponse]:
        rows = self.db.query(DeadLetterRecord).order_by(DeadLetterRecord.created_at.desc()).limit(100).all()
        return [DeadLetterResponse(id=row.id, jobId=row.job_id, alertId=row.alert_id, incidentId=row.incident_id, kind=row.kind, reason=row.reason, payload=row.payload, createdAt=row.created_at) for row in rows]

    def agent_run_traces(self) -> list[AgentRunTraceResponse]:
        rows = self.db.query(AgentRunTrace).order_by(AgentRunTrace.created_at.desc()).limit(100).all()
        return [AgentRunTraceResponse(id=row.id, alertId=row.alert_id, incidentId=row.incident_id, severity=row.severity, alertType=row.alert_type, originalPayload=loads(row.original_payload_json, {}), agentSteps=loads(row.agent_steps_json, []), retrievedKnowledge=loads(row.retrieved_knowledge_json, []), assessment=loads(row.assessment_json, {}), createdAt=row.created_at) for row in rows]

    def tool_audits(self) -> list[ToolAuditResponse]:
        rows = self.db.query(ToolAuditRecord).order_by(ToolAuditRecord.created_at.desc()).limit(100).all()
        return [ToolAuditResponse(id=row.id, jobId=row.job_id, alertId=row.alert_id, toolName=row.tool_name, policy=row.policy, allowed=row.allowed, status=row.status, reason=row.reason, payload=loads(row.payload, {}), createdAt=row.created_at, updatedAt=row.updated_at) for row in rows]

    def _alert(self, row: AlertEvent) -> AlertEventResponse:
        return AlertEventResponse(id=row.id, source=row.source.name if row.source else "", externalId=row.external_id, fingerprint=row.fingerprint, title=row.title, description=row.description, labels=loads(row.labels_json, {}), annotations=loads(row.annotations_json, {}), severity=row.severity, alertType=row.alert_type, status=row.status, startsAt=row.starts_at, endsAt=row.ends_at, createdAt=row.created_at)

    def _incident(self, row: Incident) -> IncidentResponse:
        return IncidentResponse(id=row.id, incidentKey=row.incident_key, severity=row.severity, alertType=row.alert_type, status=row.status, owner=row.owner, summary=row.summary, impact=row.impact, rootCauseHint=row.root_cause_hint, runbookHint=row.runbook_hint, alertCount=row.alert_count, lastAlertId=row.last_alert_id, acknowledgedBy=row.acknowledged_by, acknowledgedAt=row.acknowledged_at, resolvedBy=row.resolved_by, resolvedAt=row.resolved_at, createdAt=row.created_at, updatedAt=row.updated_at)
