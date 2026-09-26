"""补全的 Agent 服务层测试：模型注册表 / 模型资产 / 工具治理 / 轨迹 / 对话 harness / 自主循环。"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings


@pytest.fixture()
def db():
    """每个测试用独立的临时 sqlite 库（StaticPool 保证内存库单连接共享）。"""
    import app.models.entities  # noqa: F401  先注册全部模型类
    import app.core.database as database

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=__import__("sqlalchemy.pool", fromlist=["StaticPool"]).StaticPool,
    )
    database.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture()
def settings():
    return Settings(ai_provider="mock", tool_queue_enabled=False)


# ---------------------------------------------------------------- Agent 模型注册表

def test_agent_model_profile_falls_back_to_global_settings(settings):
    from app.services.agent_models import AgentModelRegistry

    profile = AgentModelRegistry(settings).profile_for("SafetyAgent")
    assert profile.provider == "mock"
    assert profile.temperature == settings.ai_temperature
    # 未在 .env 配置覆盖时，SafetyAgent 与全局使用同一 provider/model
    assert AgentModelRegistry(settings).profile_for("ResponseAgent").model == profile.model


def test_agent_model_profile_supports_per_agent_override(settings):
    from app.services.agent_models import AgentModelRegistry

    # 模拟 .env 只覆盖 SafetyAgent 的温度：验证逐 Agent 覆盖与全局回落并存
    object.__setattr__(settings, "agent_model_safety_temperature", 0.0)
    safety = AgentModelRegistry(settings).profile_for("SafetyAgent")
    assert safety.temperature == 0.0
    assert AgentModelRegistry(settings).profile_for("ContextAgent").temperature != 0.0


# ---------------------------------------------------------------- 微调模型资产

def test_finetuned_model_status_reports_missing_or_ready(settings):
    from pathlib import Path

    from app.services.model_assets import finetuned_model_status

    status = finetuned_model_status(settings)
    assert status["name"] == settings.finetuned_model_name
    assert status["directory"].startswith("models/")
    # 本仓库自带 Modelfile（models/evoharness-alert-qwen2.5-7b/Modelfile），GGUF 视下载情况而定
    assert status["modelfileExists"] is Path(
        settings.project_root, "models/evoharness-alert-qwen2.5-7b", "Modelfile"
    ).exists()


# ---------------------------------------------------------------- 工具治理

def test_tool_policy_blocks_p3_notification_but_allows_p0():
    from app.services.tool_governance import ToolPolicyRegistry

    allowed, _, _ = ToolPolicyRegistry.authorize("NOTIFICATION_SEND", "P0")
    blocked, reason, policy = ToolPolicyRegistry.authorize("NOTIFICATION_SEND", "P3")
    assert allowed is True
    assert blocked is False
    assert "不允许" in reason and policy is not None
    unknown, _, _ = ToolPolicyRegistry.authorize("UNKNOWN_TOOL", "P0")
    assert unknown is False


def test_tool_governance_start_job_writes_audit_record(db, settings):
    from app.core.enums import ToolJobKind, ToolJobStatus
    from app.models.entities import ToolAuditRecord, ToolJob
    from app.schemas.dtos import AlertWebhookRequest
    from app.services.alerting import AlertIngestService
    from app.services.tool_governance import ToolGovernanceService

    # 走主链路构造一条 P3 告警（AlertEvent.source 是 relationship，不能直接传字符串）
    result = AlertIngestService(db, settings).ingest_webhook(AlertWebhookRequest(
        source="webhook",
        title="单实例偶发错误日志",
        severity="P3",
        alertType="PROBLEM",
    ))
    job = ToolJob(
        alert_id=result.alert.id,
        kind=ToolJobKind.NOTIFICATION_SEND.value,
        status=ToolJobStatus.PENDING.value,
    )
    db.add(job)
    db.commit()

    service = ToolGovernanceService(db)
    record = service.start_job(job, "P3")
    assert record.allowed is False
    assert record.status == "BLOCKED"
    # require_allowed 应直接抛异常，交由 worker 走失败流程
    with pytest.raises(RuntimeError):
        service.require_allowed(job, "P3")
    finished = service.finish(record, "SKIPPED", reason="策略拦截")
    assert finished.status == "SKIPPED"
    assert db.query(ToolAuditRecord).count() == 1


# ---------------------------------------------------------------- 执行轨迹

def test_agent_trace_service_saves_chat_turn_without_alert(db, settings):
    from app.agents.factory import create_agent_runtime
    from app.models.entities import AgentRunTrace
    from app.services.trace import AgentTraceService

    agent_run = create_agent_runtime(settings).run("P0 告警：验券不可用", session_id="trace-s1")
    trace = AgentTraceService(db).save_run(alert=None, incident_id=None, agent_run=agent_run, severity="P0", alert_type="PROBLEM")

    assert trace.alert_id == 0  # 对话式排障轮次：无告警上下文
    assert trace.severity == "P0"
    steps = __import__("json").loads(trace.agent_steps_json)
    kinds = {entry["kind"] for entry in steps if isinstance(entry, dict)}
    assert "agent_event" in kinds and "agent_artifact" in kinds
    assert db.query(AgentRunTrace).count() == 1


# ---------------------------------------------------------------- 对话式排障 Harness

def test_alert_agent_harness_runs_turn_and_persists_trace(db, settings):
    from app.agents.harness import AlertAgentHarness
    from app.models.entities import AgentRunTrace, UserAccount
    from app.schemas.dtos import ChatRequest

    db.add(UserAccount(username="oncall", display_name="值班同学", password_hash="x", roles_csv="ADMIN"))
    db.commit()
    user = db.query(UserAccount).filter_by(username="oncall").one()

    harness = AlertAgentHarness(db, settings)
    outcome = harness.run(user, ChatRequest(message="P1 告警：支付成功率下跌，帮我看看"))
    assert outcome.intent == "ALERT"
    assert outcome.severity == "P1"
    assert outcome.trace_id is not None
    assert outcome.response_messages
    # 助手消息可继续写入会话记忆（Redis 不可用时静默降级，不抛错）
    harness.save_assistant_message(user, outcome.session_id, "结论：优先检查支付回调链路。")
    assert db.query(AgentRunTrace).filter(AgentRunTrace.id == outcome.trace_id).one() is not None


# ---------------------------------------------------------------- 自主 Agent 循环

def test_run_autonomous_turn_reaches_final_with_revision_budget(settings):
    from app.agents.autonomous import build_agent_services, run_autonomous_turn

    services = build_agent_services(settings, "pytest-session")
    board = run_autonomous_turn(services, "P0 告警：coupon-service 验券失败率 60%，如何排查？")

    kinds = [artifact.kind for artifact in board.artifacts]
    # 确定性事实 + 上下文 + 候选方案都必须出现
    assert "intent" in kinds and "risk" in kinds and "context" in kinds and "response_proposal" in kinds
    # 安全审查不通过时必须被修订预算兜底，最终一定有采纳结论
    assert board.final_artifact_id


def test_safety_agent_blocks_sensitive_content(settings):
    from app.agents.autonomous import AlertSafetyAgent, AgentRuntimeServices, AgentPrivateMemory
    from app.agents.events import AgentArtifact, AgentTask, CollaborationBlackboard, TaskPriority
    from app.agents.factory import create_agent_runtime
    from app.agents.registry import AgentCapability
    from app.services.agent_models import AgentModelRegistry
    from app.services.memory import RedisShortTermMemoryStore

    services = AgentRuntimeServices(
        settings=settings,
        model_registry=AgentModelRegistry(settings),
        memory=RedisShortTermMemoryStore(settings),
        private_memory=AgentPrivateMemory(settings),
        knowledge=None,
        session_id="safety-pytest",
    )
    agent_run = create_agent_runtime(settings).run("P0 告警：数据库连接失败", session_id="safety-pytest")
    proposal_payload = {
        "messages": agent_run.response_messages,
        "mode": "assessment",
    }
    # 构造一个含敏感凭据的候选方案，审查必须驳回
    from app.schemas.dtos import AiMessage

    proposal_payload["messages"] = [AiMessage(role="assistant", content="使用 sk-abcdef1234567890abcdef 调用接口重启服务即可")]
    task = AgentTask(id="t-review", title="审查", required_capabilities=frozenset({AgentCapability.SAFETY.value}), priority=TaskPriority.HIGH)
    board = CollaborationBlackboard(turn_id="t-review", model_input="x", user_input="x").add_task(task)
    # 先把候选方案放上黑板（SafetyAgent.decide 依据"存在未审查候选"来认领）
    proposal = board.add_artifact(AgentArtifact(id="prop-1", owner="AlertResponseAgent", kind="response_proposal", payload=proposal_payload, task_id=task.id))
    safety = AlertSafetyAgent(services)
    decision = safety.decide(task, proposal)
    assert decision.claim is True
    result = safety.act(task, proposal)
    review = result.artifacts[0]
    assert review.kind == "critique"
    assert "敏感凭据" in review.payload["reason"]
