from __future__ import annotations

from app.agents.factory import create_agent_runtime
from app.core.config import Settings


def test_factory_returns_event_driven_runtime_for_alert():
    runtime = create_agent_runtime(settings=Settings())

    result = runtime.run("P1 告警：coupon-service 核销接口失败率升高", session_id="s1")

    assert result.intent == "ALERT"
    assert result.priority.value == "P1"
    assert result.alert_type.value == "PROBLEM"
    assert result.requires_report is True
    assert result.collaboration_tasks
    assert result.collaboration_events


def test_runtime_keeps_plain_chat_out_of_alert_report_flow():
    runtime = create_agent_runtime(settings=Settings())

    result = runtime.run("你是什么系统？", session_id="s2")

    assert result.intent == "CHAT"
    assert result.requires_report is False
    assert "不会触发 RAG" in result.response_messages[0].content
