from __future__ import annotations

from app.agents.factory import create_agent_runtime
from app.agents.events import AgentArtifact, AgentEvent, AgentEventType, AgentTask, CollaborationBlackboard
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
    assert any(event.type.value == "FINAL_ACCEPTED" for event in result.collaboration_events)


def test_runtime_keeps_plain_chat_out_of_alert_report_flow():
    runtime = create_agent_runtime(settings=Settings())

    result = runtime.run("你是什么系统？", session_id="s2")

    assert result.intent == "CHAT"
    assert result.requires_report is False
    assert "不会触发 RAG" in result.response_messages[0].content


def test_blackboard_updates_return_new_objects():
    board = CollaborationBlackboard(turn_id="t1")
    task = AgentTask(id="task-1", title="理解输入")
    event = AgentEvent(type=AgentEventType.TASK_CREATED, actor="CoordinatorAgent", task_id=task.id)
    artifact = AgentArtifact(id="artifact-1", owner="UnderstandingAgent", kind="intent", payload={"intent": "ALERT"})

    with_task = board.add_task(task)
    with_event = with_task.append_event(event)
    with_artifact = with_event.add_artifact(artifact)

    assert board.tasks == {}
    assert board.events == ()
    assert board.artifacts == ()
    assert with_task.tasks[task.id] == task
    assert len(with_event.events) == 1
    assert with_artifact.latest_artifact("intent") == artifact
