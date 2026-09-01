from __future__ import annotations

import smtplib
import ssl
import threading
from email.message import EmailMessage
from pathlib import Path

from openpyxl import Workbook, load_workbook
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.enums import ToolStatus
from app.models.entities import AlertEvent, Incident, LedgerRecord, NotificationRecord


EXCEL_WRITE_LOCK = threading.Lock()


class ToolOrchestrationService:
    def __init__(self, db: Session, settings: Settings):
        self.db = db
        self.settings = settings

    def write_ledger(self, alert: AlertEvent) -> LedgerRecord:
        existing = self.db.query(LedgerRecord).filter(LedgerRecord.alert_id == alert.id, LedgerRecord.status == ToolStatus.SUCCESS.value).first()
        if existing is not None:
            return existing
        path = Path(self.settings.excel_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with EXCEL_WRITE_LOCK:
            if path.exists():
                workbook = load_workbook(path)
                sheet = workbook.active
            else:
                workbook = Workbook()
                sheet = workbook.active
                sheet.title = "告警台账"
                sheet.append(["告警ID", "优先级", "类型", "状态", "标题", "指纹", "创建时间"])
            sheet.append([alert.id, alert.severity, alert.alert_type, alert.status, alert.title, alert.fingerprint, alert.created_at.isoformat()])
            workbook.save(path)
        record = LedgerRecord(alert_id=alert.id, file_path=str(path), status=ToolStatus.SUCCESS.value, message="告警台账已写入")
        self.db.add(record)
        self.db.commit()
        return record

    def record_incident_upsert(self, alert: AlertEvent, incident: Incident | None) -> LedgerRecord:
        record = LedgerRecord(
            alert_id=alert.id,
            file_path="incident-db",
            status=ToolStatus.SUCCESS.value,
            message=f"事件已创建或更新：incidentId={incident.id if incident else '无'}",
        )
        self.db.add(record)
        self.db.commit()
        return record

    def send_notification(self, alert: AlertEvent, incident: Incident) -> NotificationRecord:
        existing = self.db.query(NotificationRecord).filter(NotificationRecord.alert_id == alert.id, NotificationRecord.status == ToolStatus.SUCCESS.value).first()
        if existing is not None:
            return existing
        recipient = self.settings.alert_email_to.strip() or "log"
        mode = self.settings.alert_email_delivery_mode.strip().lower()
        if mode == "log":
            return self._save(alert, incident, recipient, ToolStatus.SUCCESS.value, f"P0/P1 告警通知已记录：alertId={alert.id}, incidentId={incident.id}, deliveryMode=log")
        if mode != "smtp":
            return self._save(alert, incident, recipient, ToolStatus.FAILED.value, f"未知通知模式：{self.settings.alert_email_delivery_mode}")
        missing = self._missing_email_config()
        if missing:
            return self._save(alert, incident, recipient, ToolStatus.FAILED.value, f"邮件未发送，缺少配置：{', '.join(missing)}")
        try:
            self._send_email(alert, incident)
        except Exception as exc:
            return self._save(alert, incident, recipient, ToolStatus.FAILED.value, f"邮件发送失败：{type(exc).__name__}: {exc}")
        return self._save(alert, incident, recipient, ToolStatus.SUCCESS.value, f"P0/P1 告警邮件已发送：alertId={alert.id}")

    def _save(self, alert: AlertEvent, incident: Incident, recipient: str, status: str, message: str) -> NotificationRecord:
        record = NotificationRecord(alert_id=alert.id, incident_id=incident.id, channel="email", recipient=recipient, status=status, message=message)
        self.db.add(record)
        self.db.commit()
        return record

    def _missing_email_config(self) -> list[str]:
        missing = []
        if not self.settings.smtp_host.strip():
            missing.append("SMTP_HOST")
        if not (self.settings.alert_email_from.strip() or self.settings.smtp_username.strip()):
            missing.append("ALERT_EMAIL_FROM 或 SMTP_USERNAME")
        if not self.settings.alert_email_to.strip():
            missing.append("ALERT_EMAIL_TO")
        return missing

    def _send_email(self, alert: AlertEvent, incident: Incident) -> None:
        message = EmailMessage()
        message["Subject"] = f"{self.settings.alert_email_subject_prefix} alertId={alert.id}"
        message["From"] = self.settings.alert_email_from.strip() or self.settings.smtp_username.strip()
        message["To"] = self.settings.alert_email_to
        message.set_content("\n".join([
            "EvoHarnessAlert 检测到 P0/P1 高优先级告警，请尽快处理。",
            "",
            f"事件ID：{incident.id}",
            f"告警ID：{alert.id}",
            f"标题：{alert.title}",
            f"优先级：{alert.severity}",
            f"类型：{alert.alert_type}",
            f"状态：{alert.status}",
            f"摘要：{incident.summary}",
            f"影响：{incident.impact}",
            f"处置建议：{incident.runbook_hint}",
        ]))
        context = ssl.create_default_context()
        if self.settings.smtp_use_ssl:
            with smtplib.SMTP_SSL(self.settings.smtp_host, self.settings.smtp_port, timeout=self.settings.smtp_timeout_seconds, context=context) as server:
                self._send(server, message)
            return
        with smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port, timeout=self.settings.smtp_timeout_seconds) as server:
            server.ehlo()
            if self.settings.smtp_use_tls:
                server.starttls(context=context)
                server.ehlo()
            self._send(server, message)

    def _send(self, server: smtplib.SMTP, message: EmailMessage) -> None:
        if self.settings.smtp_username:
            server.login(self.settings.smtp_username, self.settings.smtp_password)
        server.send_message(message)
