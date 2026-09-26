from __future__ import annotations
from dataclasses import dataclass  # 数据类
from app.core.enums import Severity  # 严重级别枚举
from app.services.alerting import normalize_severity  # 严重级别归一

@dataclass(frozen=True)
class AlertAssessment:
    severity: Severity    # 归一后的严重级别
    confidence: float     # 研判置信度
    summary: str          # 研判摘要

class AlertAssessmentService:
    # 告警研判服务：对一条告警做定级与摘要
    def assess(self, severity: str, title: str) -> AlertAssessment:
        normalized = normalize_severity(severity)  # 归一严重级别
        return AlertAssessment(normalized, 0.8, f"{title} 定级为 {normalized.value}")  # 生成研判结果