"""安全审查绑定、置信度门槛、上下文路由和轮数预算。"""
import pytest

from app.agents.coordinator import EventDrivenCoordinator
from app.agents.events import AgentArtifact, CollaborationBlackboard
from app.agents.factory import create_agent_runtime
from app.core.config import Settings


@pytest.mark.parametrize("approved,confidence,reviewed,accepted", [
    (True, 0.6, "candidate", True), (True, 0.59, "candidate", False),
    (False, 0.99, "candidate", False), (True, 0.99, "old-candidate", False),
])
def test_final_acceptance_requires_matching_approved_review(approved, confidence, reviewed, accepted):
    board = CollaborationBlackboard(turn_id="turn")
    board = board.add_artifact(AgentArtifact(id="candidate", owner="ResponseAgent", kind="response_proposal", payload={"content": "建议核验日志"}, confidence=confidence))
    board = board.add_artifact(AgentArtifact(id="review", owner="SafetyAgent", kind="safety_review", payload={"approved": approved}, metadata={"responseArtifactId": reviewed}))
    result = EventDrivenCoordinator(Settings(_env_file=None, agent_final_accept_min_confidence=0.6)).try_accept_final(board)
    assert bool(result.final_artifact_id) is accepted
    assert not board.final_artifact_id


@pytest.mark.parametrize("priority,context", [("P0", True), ("P1", True), ("P2", True), ("P3", False)])
def test_context_routing_by_priority(priority, context):
    result = create_agent_runtime(settings=Settings(_env_file=None)).run("告警：下单失败", labels={"severity": priority})
    assert ("task_gather_context" in {task.id for task in result.collaboration_tasks}) is context
    assert result.priority.value == priority


def test_explicit_priority_overrides_text_guess():
    result = create_agent_runtime(settings=Settings(_env_file=None)).run("P0 告警文本", labels={"severity": "P2", "alert_type": "host"})
    assert result.priority.value == "P2"
    assert result.alert_type.value == "HOST"


def test_exhausted_round_budget_does_not_accept_unreviewed_response():
    result = create_agent_runtime(settings=Settings(_env_file=None, agent_runtime_max_steps=1)).run("P0 告警：下单失败")
    events = [event.type.value for event in result.collaboration_events]
    assert events.count("ROUND_STARTED") == 1
    assert "BUDGET_EXHAUSTED" in events
    assert "FINAL_ACCEPTED" not in events
