from __future__ import annotations

"""EvoHarnessAlert 告警助手兼容模块。

第一版告警接入主链路位于 app.services.alerting；这里保留轻量占位，避免旧骨架导入失败。
"""


def status() -> dict:
    return {"status": "READY", "domain": "alerting", "message": "告警助手模块已就绪"}
