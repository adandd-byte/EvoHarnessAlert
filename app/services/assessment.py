from __future__ import annotations
from dataclasses import dataclass
from app.core.enums import Severity
from app.services.alerting import normalize_severity

@dataclass(frozen=True)
class AlertAssessment:
    severity: Severity
    confidence: float
    summary: str

class AlertAssessmentService:
    def assess(self, severity: str, title: str) -> AlertAssessment:
        normalized = normalize_severity(severity)
        return AlertAssessment(normalized, 0.8, f"{title} 定级为 {normalized.value}")
