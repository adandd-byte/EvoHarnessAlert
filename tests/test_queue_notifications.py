"""通过会话和邮件替身验证状态转换，不连接数据库或发送邮件。"""
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.core.config import Settings
from app.models.entities import DeadLetterRecord
from app.services import tool_queue
from app.services.tool_queue import RateLimiter, ToolQueueWorker
from app.services.tools import ToolOrchestrationService


@pytest.fixture
def worker():
    value = ToolQueueWorker(Settings(_env_file=None, tool_queue_enabled=False, tool_queue_retry_delay_seconds=10))
    yield value
    value.stop()


def job(**overrides):
    values = dict(id=1, alert_id=2, incident_id=3, kind="NOTIFICATION_SEND", status="RUNNING", attempts=0, max_attempts=3, last_error="")
    values.update(overrides)
    return SimpleNamespace(**values)


def test_rate_limit_expires_at_sixty_seconds(monkeypatch):
    clock = Mock(return_value=100.0)
    monkeypatch.setattr(tool_queue.time, "monotonic", clock)
    limiter = RateLimiter(1)
    assert limiter.allow() == (True, 0)
    clock.return_value = 159
    assert limiter.allow() == (False, 1)
    clock.return_value = 160
    assert limiter.allow() == (True, 0)


def test_disabled_rate_limit_always_allows():
    limiter = RateLimiter(0)
    assert all(limiter.allow() == (True, 0) for _ in range(100))


@pytest.mark.parametrize("attempts,expected", [(1, "PENDING"), (2, "PENDING"), (3, "DEAD")])
def test_notification_failure_retries_then_dead_letters(worker, attempts, expected):
    record = job(attempts=attempts)
    db = Mock()
    db.get.return_value = record
    before = datetime.utcnow()
    worker._fail_or_dead_letter(db, record.id, RuntimeError("SMTP 超时"))
    assert record.status == expected
    assert "SMTP 超时" in record.last_error
    dead = [call.args[0] for call in db.add.call_args_list if isinstance(call.args[0], DeadLetterRecord)]
    if expected == "DEAD":
        assert len(dead) == 1
        assert (dead[0].job_id, dead[0].alert_id) == (1, 2)
    else:
        assert not dead
        assert before + timedelta(seconds=10 * attempts) <= record.run_after <= datetime.utcnow() + timedelta(seconds=10 * attempts)
    db.commit.assert_called_once()


def test_rate_limited_job_does_not_consume_attempt(worker, monkeypatch):
    record = job()
    db = Mock()
    db.get.return_value = record
    monkeypatch.setattr(tool_queue, "SessionLocal", lambda: db)
    worker.email_limiter.allow = Mock(return_value=(False, 15))
    worker._execute = Mock()
    worker._run_job(record.id)
    assert record.attempts == 0
    assert record.status == "PENDING"
    worker._execute.assert_not_called()
    db.close.assert_called_once()


def test_successful_job_clears_error_and_increments_attempt(worker, monkeypatch):
    record = job(last_error="上次失败")
    db = Mock()
    db.get.return_value = record
    monkeypatch.setattr(tool_queue, "SessionLocal", lambda: db)
    worker._execute = Mock()
    worker._run_job(record.id)
    assert (record.status, record.attempts, record.last_error) == ("SUCCESS", 1, "")
    worker._execute.assert_called_once_with(db, record)


def test_restart_recovers_running_jobs(worker, monkeypatch):
    records = [job(id=1), job(id=2)]
    db = Mock()
    db.query.return_value.filter.return_value.all.return_value = records
    monkeypatch.setattr(tool_queue, "SessionLocal", lambda: db)
    worker._recover_running_jobs()
    assert all(record.status == "PENDING" and "恢复" in record.last_error for record in records)
    db.commit.assert_called_once()
    db.close.assert_called_once()


@pytest.mark.parametrize("mode,configured,error,expected", [
    ("log", False, None, "SUCCESS"), ("invalid", False, None, "FAILED"),
    ("smtp", False, None, "FAILED"), ("smtp", True, RuntimeError("发送超时"), "FAILED"),
    ("smtp", True, None, "SUCCESS"),
])
def test_notification_delivery_modes(mode, configured, error, expected):
    db = Mock()
    db.query.return_value.filter.return_value.first.return_value = None
    settings = Settings(_env_file=None, alert_email_delivery_mode=mode, smtp_host="smtp.example.test" if configured else "",
                        smtp_username="", alert_email_from="oncall@example.test" if configured else "",
                        alert_email_to="team@example.test" if configured else "")
    service = ToolOrchestrationService(db, settings)
    service._send_email = Mock(side_effect=error)
    result = service.send_notification(SimpleNamespace(id=1), SimpleNamespace(id=2))
    assert result.status == expected
    assert service._send_email.call_count == int(mode == "smtp" and configured)
    db.add.assert_called_once_with(result)


def test_successful_notification_is_not_sent_twice():
    db = Mock()
    existing = SimpleNamespace(status="SUCCESS")
    db.query.return_value.filter.return_value.first.return_value = existing
    service = ToolOrchestrationService(db, Settings(_env_file=None))
    service._send_email = Mock()
    assert service.send_notification(SimpleNamespace(id=1), SimpleNamespace(id=2)) is existing
    service._send_email.assert_not_called()
    db.add.assert_not_called()
