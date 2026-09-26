"""告警归一化、聚合指纹、Prometheus 转换与入队策略的单元测试。"""
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.core.config import Settings
from app.services.alerting import (
    AlertIngestService, build_fingerprint, max_severity, normalize_alert_type,
    normalize_severity, normalize_status, parse_dt,
)


@pytest.mark.parametrize("raw,expected", [
    (" P0 ", "P0"), ("emergency", "P0"), ("critical", "P1"),
    ("WARNING", "P2"), ("info", "P3"), (None, "P2"), ("unknown", "P2"),
])
def test_priority_normalization(raw, expected):
    assert normalize_severity(raw).value == expected


@pytest.mark.parametrize("raw,expected", [
    ("故障", "PROBLEM"), (" BIZ ", "BUSINESS"), ("发布", "EVENT"),
    ("机器", "HOST"), (None, "PROBLEM"),
])
def test_type_normalization(raw, expected):
    assert normalize_alert_type(raw).value == expected


@pytest.mark.parametrize("raw,expected", [("resolved", "RESOLVED"), (" OK ", "RESOLVED"), (None, "FIRING")])
def test_status_normalization(raw, expected):
    assert normalize_status(raw).value == expected


@pytest.mark.parametrize("left", ["P0", "P1", "P2", "P3"])
@pytest.mark.parametrize("right", ["P0", "P1", "P2", "P3"])
def test_incident_priority_never_downgrades(left, right):
    assert max_severity(left, right) == min(left, right)


def test_fingerprint_ignores_instance_churn_and_label_order():
    first = {"service": "order", "env": "prod", "instance": "a", "pod": "old", "timestamp": "1"}
    second = {"timestamp": "2", "pod": "new", "instance": "b", "env": "prod", "service": "order"}
    assert build_fingerprint("webhook", "下单失败", first) == build_fingerprint("webhook", "下单失败", second)
    for changed in ({**first, "service": "payment"}, {**first, "env": "test"}):
        assert build_fingerprint("webhook", "下单失败", first) != build_fingerprint("webhook", "下单失败", changed)
    assert build_fingerprint("webhook", "下单失败", first, "PROBLEM") != build_fingerprint("webhook", "下单失败", first, "EVENT")


@pytest.mark.parametrize("timestamp", ["2026-09-25T10:00:00+08:00", "2026-09-25T02:00:00Z"])
def test_timestamps_represent_same_utc_instant(timestamp):
    assert parse_dt(timestamp) == datetime(2026, 9, 25, 2)


@pytest.mark.parametrize("payload", [{}, {"alerts": []}, {"alerts": "wrong"}])
def test_prometheus_rejects_missing_alert_array(payload):
    service = AlertIngestService(Mock(), Settings(_env_file=None))
    with pytest.raises(ValueError, match="alerts"):
        service.ingest_prometheus(payload)


def test_prometheus_preserves_recovery_priority_type_and_raw_input():
    service = AlertIngestService(Mock(), Settings(_env_file=None))
    service.ingest_webhook = Mock(return_value="ingested")
    item = {"fingerprint": "fp-1", "labels": {"alertname": "磁盘异常", "priority": "P1", "alert_type": "host"},
            "annotations": {"summary": "磁盘已恢复"}, "endsAt": "2026-09-25T02:00:00Z"}
    assert service.ingest_prometheus({"status": "resolved", "alerts": [item]}) == ["ingested"]
    request, raw = service.ingest_webhook.call_args.args
    assert (request.source, request.title, request.status, request.severity, request.alertType) == ("prometheus", "磁盘已恢复", "resolved", "P1", "host")
    assert request.fingerprint == request.externalId == "fp-1"
    assert request.endsAt == datetime(2026, 9, 25, 2)
    assert raw == item


@pytest.mark.parametrize("priority,has_incident,expected", [
    ("P0", True, ["LEDGER_WRITE", "INCIDENT_UPSERT", "NOTIFICATION_SEND"]),
    ("P1", True, ["LEDGER_WRITE", "INCIDENT_UPSERT", "NOTIFICATION_SEND"]),
    ("P2", True, ["LEDGER_WRITE", "INCIDENT_UPSERT"]),
    ("P3", False, ["LEDGER_WRITE"]), ("P0", False, ["LEDGER_WRITE"]),
])
def test_job_plan_by_priority(priority, has_incident, expected):
    db = Mock()
    service = AlertIngestService(db, Settings(_env_file=None, tool_queue_max_attempts=3))
    jobs = service._enqueue(SimpleNamespace(id=1, severity=priority), SimpleNamespace(id=2) if has_incident else None)
    assert jobs == expected
    records = [call.args[0] for call in db.add.call_args_list]
    assert [record.kind for record in records] == expected
    assert all(record.max_attempts == 3 and record.status == "PENDING" for record in records)
