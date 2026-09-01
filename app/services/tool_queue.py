from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from app.core.config import Settings
from app.core.database import SessionLocal
from app.core.enums import ToolJobKind, ToolJobStatus, ToolStatus
from app.models.entities import AlertEvent, DeadLetterRecord, Incident, ToolJob
from app.services.tools import ToolOrchestrationService

logger = logging.getLogger(__name__)


class RateLimiter:
    def __init__(self, limit_per_minute: int):
        self.limit = max(0, limit_per_minute)
        self.events: deque[float] = deque()
        self.lock = threading.Lock()

    def allow(self) -> tuple[bool, float]:
        if self.limit <= 0:
            return True, 0.0
        now_ts = time.monotonic()
        with self.lock:
            while self.events and now_ts - self.events[0] >= 60.0:
                self.events.popleft()
            if len(self.events) < self.limit:
                self.events.append(now_ts)
                return True, 0.0
            return False, max(1.0, 60.0 - (now_ts - self.events[0]))


class ToolQueueWorker:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.stop_event = threading.Event()
        self.dispatcher: threading.Thread | None = None
        self.excel_executor = ThreadPoolExecutor(max_workers=max(1, settings.tool_queue_excel_workers), thread_name_prefix="evo-ledger")
        self.email_executor = ThreadPoolExecutor(max_workers=max(1, settings.tool_queue_email_workers), thread_name_prefix="evo-notify")
        self.email_limiter = RateLimiter(settings.alert_email_rate_limit_per_minute)

    def start(self) -> None:
        if not self.settings.tool_queue_enabled or self.dispatcher is not None:
            return
        self._recover_running_jobs()
        self.dispatcher = threading.Thread(target=self._loop, name="evo-tool-dispatcher", daemon=True)
        self.dispatcher.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.dispatcher is not None:
            self.dispatcher.join(timeout=5)
        self.excel_executor.shutdown(wait=False, cancel_futures=True)
        self.email_executor.shutdown(wait=False, cancel_futures=True)

    def _loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                self._dispatch_once()
            except Exception:
                logger.exception("工具队列调度失败")
            self.stop_event.wait(self.settings.tool_queue_poll_interval_seconds)

    def _dispatch_once(self) -> None:
        db = SessionLocal()
        try:
            jobs = db.query(ToolJob).filter(ToolJob.status == ToolJobStatus.PENDING.value, ToolJob.run_after <= datetime.utcnow()).order_by(ToolJob.created_at.asc()).limit(self.settings.tool_queue_batch_size).all()
            for job in jobs:
                job.status = ToolJobStatus.RUNNING.value
                job.updated_at = datetime.utcnow()
                db.add(job)
                db.commit()
                self._executor_for(job).submit(self._run_job, job.id)
        finally:
            db.close()

    def _executor_for(self, job: ToolJob) -> ThreadPoolExecutor:
        return self.email_executor if job.kind == ToolJobKind.NOTIFICATION_SEND.value else self.excel_executor

    def _run_job(self, job_id: int) -> None:
        db = SessionLocal()
        try:
            job = db.get(ToolJob, job_id)
            if job is None or job.status != ToolJobStatus.RUNNING.value:
                return
            if job.kind == ToolJobKind.NOTIFICATION_SEND.value:
                allowed, retry_after = self.email_limiter.allow()
                if not allowed:
                    self._requeue(db, job, "通知限流中，稍后重试", retry_after)
                    return
            job.attempts += 1
            job.updated_at = datetime.utcnow()
            db.add(job)
            db.commit()
            self._execute(db, job)
            job.status = ToolJobStatus.SUCCESS.value
            job.last_error = ""
            job.updated_at = datetime.utcnow()
            db.add(job)
            db.commit()
        except Exception as exc:
            self._fail_or_dead_letter(db, job_id, exc)
        finally:
            db.close()

    def _execute(self, db, job: ToolJob) -> None:
        alert = db.get(AlertEvent, job.alert_id)
        if alert is None:
            raise RuntimeError(f"告警不存在：{job.alert_id}")
        incident = db.get(Incident, job.incident_id) if job.incident_id else None
        tools = ToolOrchestrationService(db, self.settings)
        if job.kind == ToolJobKind.LEDGER_WRITE.value:
            record = tools.write_ledger(alert)
            if record.status != ToolStatus.SUCCESS.value:
                raise RuntimeError(record.message)
            return
        if job.kind == ToolJobKind.INCIDENT_UPSERT.value:
            tools.record_incident_upsert(alert, incident)
            return
        if job.kind == ToolJobKind.NOTIFICATION_SEND.value:
            if incident is None:
                raise RuntimeError("发送通知前事件不存在")
            record = tools.send_notification(alert, incident)
            if record.status != ToolStatus.SUCCESS.value:
                raise RuntimeError(record.message)
            return
        raise RuntimeError(f"未知任务类型：{job.kind}")

    def _requeue(self, db, job: ToolJob, reason: str, delay_seconds: float) -> None:
        job.status = ToolJobStatus.PENDING.value
        job.last_error = reason
        job.run_after = datetime.utcnow() + timedelta(seconds=max(1.0, delay_seconds))
        job.updated_at = datetime.utcnow()
        db.add(job)
        db.commit()

    def _fail_or_dead_letter(self, db, job_id: int, exc: Exception) -> None:
        job = db.get(ToolJob, job_id)
        if job is None:
            return
        message = f"{type(exc).__name__}: {exc}"
        job.last_error = message
        job.updated_at = datetime.utcnow()
        if job.attempts >= job.max_attempts:
            job.status = ToolJobStatus.DEAD.value
            db.add(DeadLetterRecord(job_id=job.id, alert_id=job.alert_id, incident_id=job.incident_id, kind=job.kind, reason=message, payload=json.dumps({"alertId": job.alert_id, "kind": job.kind}, ensure_ascii=False)))
        else:
            job.status = ToolJobStatus.PENDING.value
            job.run_after = datetime.utcnow() + timedelta(seconds=self.settings.tool_queue_retry_delay_seconds * max(1, job.attempts))
        db.add(job)
        db.commit()

    def _recover_running_jobs(self) -> None:
        db = SessionLocal()
        try:
            for job in db.query(ToolJob).filter(ToolJob.status == ToolJobStatus.RUNNING.value).all():
                job.status = ToolJobStatus.PENDING.value
                job.last_error = "服务重启后恢复未完成任务"
                job.run_after = datetime.utcnow()
                job.updated_at = datetime.utcnow()
                db.add(job)
            db.commit()
        finally:
            db.close()


_worker: ToolQueueWorker | None = None


def get_tool_queue_worker(settings: Settings) -> ToolQueueWorker:
    global _worker
    if _worker is None:
        _worker = ToolQueueWorker(settings)
    return _worker
