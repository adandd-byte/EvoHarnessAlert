class McpToolError(RuntimeError): pass

class EvoHarnessMcpToolClient:
    async def handle_alert(self, alert_id: int, severity: str | None) -> list[str]:
        return [f"queued-alert:{alert_id}:{severity or 'UNKNOWN'}"]
