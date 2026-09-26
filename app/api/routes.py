from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import current_user, require_admin
from app.models.entities import UserAccount
from app.schemas.dtos import AckRequest, AlertIngestResponse, AlertWebhookRequest, ChatRequest, IncidentNoteRequest, KnowledgeIngestRequest, KnowledgeIngestResponse, ResolveRequest, authority
from app.services.alerting import AlertIngestService, IncidentService
from app.services.chat import ChatService
from app.services.knowledge import KnowledgeService
from app.services.model_assets import finetuned_model_status
from app.services.report import ReportService
from app.services.skills import AlertSkillLibrary

router = APIRouter()


@router.get("/actuator/health")
def health():
    return {"status": "UP", "service": "EvoHarnessAlert"}


@router.get("/api/profile")
def profile(user: Annotated[UserAccount, Depends(current_user)]):
    return {"id": user.id, "username": user.username, "displayName": user.display_name, "roles": [authority(role) for role in user.roles]}


@router.post("/api/alerts/webhook")
async def ingest_webhook(request: AlertWebhookRequest, db: Annotated[Session, Depends(get_db)]):
    result = AlertIngestService(db, get_settings()).ingest_webhook(request)
    return AlertIngestResponse(alertId=result.alert.id, incidentId=result.incident.id if result.incident else None, severity=result.alert.severity, alertType=result.alert.alert_type, status=result.alert.status, fingerprint=result.alert.fingerprint, queuedJobs=result.queued_jobs, message="告警已接收")


@router.post("/api/alerts/prometheus")
async def ingest_prometheus(payload: dict[str, Any], db: Annotated[Session, Depends(get_db)]):
    try:
        results = AlertIngestService(db, get_settings()).ingest_prometheus(payload)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"message": f"已接收 {len(results)} 条 Prometheus 告警", "items": [AlertIngestResponse(alertId=item.alert.id, incidentId=item.incident.id if item.incident else None, severity=item.alert.severity, alertType=item.alert.alert_type, status=item.alert.status, fingerprint=item.alert.fingerprint, queuedJobs=item.queued_jobs, message="告警已接收").model_dump() for item in results]}


@router.get("/api/agent/status")
def agent_status(user: Annotated[UserAccount, Depends(current_user)]):
    settings = get_settings()
    provider = settings.ai_provider.lower()
    model = settings.ollama_model if provider == "ollama" else settings.openai_model if provider == "openai" else "mock"
    return {
        "provider": provider,
        "model": model,
        "realModelEnabled": provider in {"ollama", "openai"},
        # 微调模型资产自检：GGUF/Modelfile 是否就绪、多大、怎么注册进 Ollama
        "finetunedModel": finetuned_model_status(settings),
        "agentFramework": {"requested": settings.agent_framework, "active": "event_driven_multi_agent", "available": ["event_driven_multi_agent"], "fallback": False},
        "agents": [
            {"name": "CoordinatorAgent", "status": "READY", "description": "确定性归一、任务板调度与最终采纳"},
            {"name": "ContextAgent", "status": "READY", "description": "检索 runbook、知识库和会话记忆"},
            {"name": "ResponseAgent", "status": "READY", "description": "生成摘要、影响判断和处置建议"},
            {"name": "SafetyAgent", "status": "READY", "description": "审查候选回复的安全性与止血建议"},
        ],
        "skills": AlertSkillLibrary.status_items(),
        "loop": {
            "type": "event-driven-multi-agent",
            "maxSteps": settings.agent_runtime_max_steps,
            "maxClaimsPerRound": settings.agent_runtime_max_claims_per_round,
            "maxClaimsPerAgent": settings.agent_runtime_max_claims_per_agent,
            "finalAcceptMinConfidence": settings.agent_final_accept_min_confidence,
            "scheduler": "claim-based-actor-runtime",
        },
    }


@router.post("/api/chat/stream")
def chat_stream(request: ChatRequest, user: Annotated[UserAccount, Depends(current_user)], db: Annotated[Session, Depends(get_db)]):
    """对话式排障（SSE 流式）：值班人员贴告警原文或追问根因。"""
    service = ChatService(db, get_settings())
    return StreamingResponse(service.stream_chat(user, request), media_type="text/event-stream")


@router.get("/api/admin/alerts")
def admin_alerts(_: Annotated[UserAccount, Depends(require_admin)], db: Annotated[Session, Depends(get_db)]):
    return ReportService(db).alerts()


@router.get("/api/admin/incidents")
def admin_incidents(_: Annotated[UserAccount, Depends(require_admin)], db: Annotated[Session, Depends(get_db)]):
    return ReportService(db).incidents()


@router.post("/api/admin/incidents/{incident_id}/ack")
def ack_incident(incident_id: int, request: AckRequest, _: Annotated[UserAccount, Depends(require_admin)], db: Annotated[Session, Depends(get_db)]):
    try:
        return ReportService(db)._incident(IncidentService(db).acknowledge(incident_id, request.actor, request.note))
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/api/admin/incidents/{incident_id}/resolve")
def resolve_incident(incident_id: int, request: ResolveRequest, _: Annotated[UserAccount, Depends(require_admin)], db: Annotated[Session, Depends(get_db)]):
    try:
        return ReportService(db)._incident(IncidentService(db).resolve(incident_id, request.actor, request.note))
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/api/admin/incidents/{incident_id}/notes")
def add_incident_note(incident_id: int, request: IncidentNoteRequest, _: Annotated[UserAccount, Depends(require_admin)], db: Annotated[Session, Depends(get_db)]):
    try:
        row = IncidentService(db).add_note(incident_id, request.actor, request.note)
        return {"id": row.id, "incidentId": row.incident_id, "actor": row.actor, "note": row.note, "createdAt": row.created_at}
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/api/admin/incidents/{incident_id}/notes")
def incident_notes(incident_id: int, _: Annotated[UserAccount, Depends(require_admin)], db: Annotated[Session, Depends(get_db)]):
    return ReportService(db).incident_notes(incident_id)


@router.get("/api/admin/ledger-records")
def admin_ledger(_: Annotated[UserAccount, Depends(require_admin)], db: Annotated[Session, Depends(get_db)]):
    return ReportService(db).ledger_records()


@router.get("/api/admin/notifications")
def admin_notifications(_: Annotated[UserAccount, Depends(require_admin)], db: Annotated[Session, Depends(get_db)]):
    return ReportService(db).notification_records()


@router.get("/api/admin/tool-jobs")
def admin_tool_jobs(_: Annotated[UserAccount, Depends(require_admin)], db: Annotated[Session, Depends(get_db)]):
    return ReportService(db).tool_jobs()


@router.get("/api/admin/dead-letters")
def admin_dead_letters(_: Annotated[UserAccount, Depends(require_admin)], db: Annotated[Session, Depends(get_db)]):
    return ReportService(db).dead_letters()


@router.get("/api/admin/agent-traces")
def admin_agent_traces(_: Annotated[UserAccount, Depends(require_admin)], db: Annotated[Session, Depends(get_db)]):
    return ReportService(db).agent_run_traces()


@router.get("/api/admin/tool-audits")
def admin_tool_audits(_: Annotated[UserAccount, Depends(require_admin)], db: Annotated[Session, Depends(get_db)]):
    return ReportService(db).tool_audits()


@router.post("/api/admin/knowledge")
def ingest_knowledge(request: KnowledgeIngestRequest, _: Annotated[UserAccount, Depends(require_admin)], db: Annotated[Session, Depends(get_db)]):
    chunks = KnowledgeService(db, get_settings()).ingest(request.source, request.content)
    return KnowledgeIngestResponse(source=request.source, chunks=chunks)


@router.get("/api/admin/knowledge/status")
def knowledge_status(_: Annotated[UserAccount, Depends(require_admin)], db: Annotated[Session, Depends(get_db)]):
    return KnowledgeService(db, get_settings()).status()


@router.post("/api/admin/knowledge/file")
async def ingest_file(_: Annotated[UserAccount, Depends(require_admin)], db: Annotated[Session, Depends(get_db)], file: UploadFile = File(...)):
    chunks = KnowledgeService(db, get_settings()).ingest_file(file.filename or "uploaded-file", await file.read())
    return KnowledgeIngestResponse(source=file.filename or "uploaded-file", chunks=chunks)
