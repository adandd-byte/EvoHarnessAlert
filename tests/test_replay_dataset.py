"""验证第19章合成回放数据的契约与实际规则路由，不代表生产金标验收。"""
import json
from collections import Counter
from pathlib import Path

import pytest

from app.agents.factory import create_agent_runtime
from app.core.config import Settings
from app.schemas.dtos import AlertWebhookRequest
from scripts.build_replay_dataset import build_dataset

DATA = json.loads((Path(__file__).resolve().parents[1] / "app/static/fixtures/alert-replay-v1.json").read_text())


def test_dataset_is_reproducible_and_covers_spec_scenarios():
    assert DATA == build_dataset()
    cases = DATA["cases"]
    assert len(cases) == len({case["id"] for case in cases}) == 100
    assert Counter(case["scenario"] for case in cases) == {i: 5 for i in range(1, 21)}
    assert Counter(case["alert"]["severity"] for case in cases) == {"P0": 25, "P1": 25, "P2": 25, "P3": 25}
    assert {case["expected"]["route"] for case in cases} == {"PROBLEM", "BUSINESS", "EVENT", "HOST"}


@pytest.mark.parametrize("case", DATA["cases"], ids=lambda case: case["id"])
def test_frozen_case_contract_and_runtime_routing(case):
    request = AlertWebhookRequest(**case["alert"])
    result = create_agent_runtime(settings=Settings(_env_file=None, ai_provider="mock")).run(
        request.description, labels={**request.labels, "severity": request.severity, "alert_type": request.alertType})
    assert result.intent == "ALERT"
    assert result.priority.value == case["expected"]["priority"]
    assert result.alert_type.value == case["expected"]["route"]
    assert case["provenance"].startswith("synthetic") and case["humanReviewed"] is False
    snapshots = {item["id"]: item for item in case["toolSnapshots"]}
    assert len(snapshots) == len(case["toolSnapshots"])
    for ref in case["expected"]["evidenceRefs"]:
        assert snapshots[ref]["status"] == "SUCCESS"
    for fact in case["expected"]["artifact"]["facts"]:
        assert fact["evidenceRefs"]
        assert set(fact["evidenceRefs"]) <= set(case["expected"]["evidenceRefs"])
    assert case["expected"]["confirmedRootCause"] is False
    assert case["authorization"]["writeAllowed"] is False
    if case["variant"] == "tenant_denied":
        assert case["expected"]["outcome"] == "ACCESS_DENIED"
        assert not case["expected"]["evidenceRefs"]
        assert all(item["status"] == "DENIED" and item["result"] == {} for item in snapshots.values())
    if case["variant"] == "timeout":
        assert any(item["status"] == "TIMEOUT" for item in snapshots.values())
    if case["variant"] == "conflict":
        assert case["expected"]["outcome"] == "CONFLICT"
        if case['scenario'] == 1:
            assert any('不同实例组' in gap for gap in case['expected']['artifact']['gaps'])
        else:
            assert "证据冲突" in case["expected"]["artifact"]["gaps"]


def test_payment_business_contract_and_missing_evidence():
    for case in DATA['cases']:
        if case['scenario'] != 1:
            continue
        assert case['versions']['fixture'] == '1.1.0'
        assert case['businessContext']['serviceName'] == '支付服务'
        assert '全量' not in case['alert']['title']
        assert case['alert']['labels']['env'] == 'staging'
        assert case['alert']['startsAt']
        assert case['expected']['actionProposal'] != '回滚支付服务'
        if case['variant'] != 'tenant_denied':
            assert all(ev['status'] == 'MISSING' and not ev['result'] for ev in case['toolSnapshots'][2:])
    if case["variant"] == "approval":
        assert case["expected"]["requiresApproval"]
        assert case["expected"]["outcome"] == "WAITING_APPROVAL"
