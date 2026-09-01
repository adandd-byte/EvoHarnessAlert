from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models.entities import AlertEvent, Incident
from app.services.tools import ToolOrchestrationService

try:
    from mcp.server.fastmcp import FastMCP
except Exception as exc:
    raise RuntimeError("缺少 mcp 依赖，无法启动 EvoHarnessAlert 工具服务") from exc

mcp = FastMCP("evoharness-alert-tools")

@mcp.tool()
def evo_alert_ledger_write(alert_id: int) -> str:
    db = SessionLocal()
    try:
        alert = db.get(AlertEvent, alert_id)
        if alert is None: raise RuntimeError("告警不存在")
        return ToolOrchestrationService(db, get_settings()).write_ledger(alert).message
    finally:
        db.close()

@mcp.tool()
def evo_alert_notify(alert_id: int, incident_id: int) -> str:
    db = SessionLocal()
    try:
        alert = db.get(AlertEvent, alert_id); incident = db.get(Incident, incident_id)
        if alert is None or incident is None: raise RuntimeError("告警或事件不存在")
        return ToolOrchestrationService(db, get_settings()).send_notification(alert, incident).message
    finally:
        db.close()

if __name__ == "__main__": mcp.run()
