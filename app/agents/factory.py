from __future__ import annotations


class AlertRuntimePlaceholder:
    def run(self, *args, **kwargs):
        raise RuntimeError("告警接入主链路使用 app.services.alerting，不再通过聊天 runtime 执行。")


def create_agent_runtime(*args, **kwargs) -> AlertRuntimePlaceholder:
    return AlertRuntimePlaceholder()


def agent_framework_status(settings) -> dict:
    return {"requested": getattr(settings, "agent_framework", "event_driven_multi_agent"), "active": "event_driven_multi_agent", "available": ["event_driven_multi_agent"], "fallback": False}
