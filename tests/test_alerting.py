from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.core.database import Base, get_db
from app.core.enums import AlertType, Severity, ToolJobKind
from app.main import app
from app.models.entities import Incident, ToolJob
from app.schemas.dtos import AlertWebhookRequest
from app.services.alerting import AlertIngestService, normalize_alert_type, normalize_severity
from app.services.privacy import PrivacySanitizer
from app.services.skills import AlertSkillRegistry


def test_severity_mapping():
    assert normalize_severity("P0") == Severity.P0
    assert normalize_severity("P1") == Severity.P1
    assert normalize_severity("warning") == Severity.P2
    assert normalize_severity("low") == Severity.P3


def test_alert_type_mapping():
    assert normalize_alert_type("problem") == AlertType.PROBLEM
    assert normalize_alert_type("业务") == AlertType.BUSINESS
    assert normalize_alert_type("host") == AlertType.HOST


def test_webhook_request_requires_title():
    with pytest.raises(Exception):
        AlertWebhookRequest(source="manual")


def test_alert_privacy_sanitizer_masks_common_secrets():
    raw = {
        "text": "联系人 13812345678 dev@example.com password=abc123 token:secret-token 10.1.2.3",
        "nested": ["Authorization: Bearer abc.def"],
    }
    sanitized = PrivacySanitizer().sanitize_data(raw)
    rendered = str(sanitized)
    assert "13812345678" not in rendered
    assert "dev@example.com" not in rendered
    assert "abc123" not in rendered
    assert "10.1.2.3" not in rendered
    assert "[已脱敏]" in rendered


def test_builtin_skills_are_ready():
    statuses = AlertSkillRegistry().status_items()
    assert statuses
    assert {item["status"] for item in statuses} == {"READY"}
    assert all(item["workflow"] for item in statuses)


@pytest.fixture()
def mysql_client(tmp_path):
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("未配置 TEST_DATABASE_URL，跳过 MySQL 集成测试")
    if not url.startswith("mysql"):
        pytest.fail("TEST_DATABASE_URL 必须指向 MySQL，例如 mysql+pymysql://evoalert:evoalert@127.0.0.1:13306/evoharness_alert_test?charset=utf8mb4")

    engine = create_engine(url, pool_pre_ping=True, pool_recycle=3600)
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    settings = get_settings()
    settings.database_url = url
    settings.excel_path = str(tmp_path / "ledger.xlsx")
    settings.tool_queue_enabled = False
    settings.alert_email_delivery_mode = "log"

    def override_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    yield TestClient(app), TestingSessionLocal, settings
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def test_health(mysql_client):
    client, _, _ = mysql_client
    response = client.get("/actuator/health")
    assert response.status_code == 200
    assert response.json()["service"] == "EvoHarnessAlert"


def test_prometheus_ingest_creates_incident_and_jobs(mysql_client):
    client, _, _ = mysql_client
    payload = {
        "status": "firing",
        "alerts": [{
            "status": "firing",
            "labels": {"alertname": "HighErrorRate", "severity": "P1", "alert_type": "problem", "service": "checkout-api", "instance": "pod-1"},
            "annotations": {"summary": "API 错误率过高", "description": "5xx 超过阈值"},
            "fingerprint": "fp-p1-1",
        }],
    }
    response = client.post("/api/alerts/prometheus", json=payload)
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["severity"] == "P1"
    assert item["alertType"] == "PROBLEM"
    assert item["incidentId"] is not None
    assert set(item["queuedJobs"]) == {"LEDGER_WRITE", "INCIDENT_UPSERT", "NOTIFICATION_SEND"}


def test_same_fingerprint_correlates_to_one_incident(mysql_client):
    _, SessionLocal, settings = mysql_client
    db = SessionLocal()
    try:
        service = AlertIngestService(db, settings)
        req = AlertWebhookRequest(source="manual", title="CPU 使用率过高", severity="P2", alertType="problem", fingerprint="same-fp", labels={"service": "api"})
        first = service.ingest_webhook(req)
        second = service.ingest_webhook(req)
        assert first.incident is not None
        assert second.incident is not None
        assert first.incident.id == second.incident.id
        assert db.query(Incident).count() == 1
        assert db.query(ToolJob).filter(ToolJob.kind == ToolJobKind.INCIDENT_UPSERT.value).count() == 2
    finally:
        db.close()
