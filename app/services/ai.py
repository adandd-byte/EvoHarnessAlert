from __future__ import annotations

import json
from typing import Iterable

import httpx

from app.core.config import Settings
from app.schemas.dtos import AiMessage


class PromptTemplates:
    @staticmethod
    def alert_assessment(payload: str) -> list[AiMessage]:
        return [AiMessage(role="system", content="你是 EvoHarnessAlert 告警助手。只返回 JSON，字段包含 summary、impact、rootCauseHint、runbookHint。"), AiMessage(role="user", content=payload)]


class AiClient:
    def __init__(self, settings: Settings): self.settings = settings
    def complete(self, messages: list[AiMessage]) -> str:
        provider = self.settings.ai_provider.lower()
        if provider == "ollama": return self._ollama(messages)
        if provider == "openai": return self._openai(messages)
        return self._mock(messages)
    async def stream(self, messages: list[AiMessage]):
        for chunk in split_text(self.complete(messages), 24): yield chunk
    def _ollama(self, messages: list[AiMessage]) -> str:
        response = httpx.post(f"{self.settings.ollama_base_url}/api/chat", json={"model": self.settings.ollama_model, "messages": [m.model_dump() for m in messages], "stream": False}, timeout=60); response.raise_for_status(); return response.json()["message"]["content"]
    def _openai(self, messages: list[AiMessage]) -> str:
        response = httpx.post(f"{self.settings.openai_base_url}/chat/completions", headers={"Authorization": f"Bearer {self.settings.openai_api_key}"}, json={"model": self.settings.openai_model, "messages": [m.model_dump() for m in messages], "temperature": self.settings.ai_temperature, "max_tokens": self.settings.ai_max_tokens}, timeout=60); response.raise_for_status(); return response.json()["choices"][0]["message"]["content"]
    def _mock(self, messages: list[AiMessage]) -> str:
        return json.dumps({"summary": "告警已完成基础研判", "impact": "请结合指标和日志确认影响范围", "rootCauseHint": "优先检查最近发布、依赖状态和资源水位", "runbookHint": "先止血，再定位；必要时升级给值班负责人"}, ensure_ascii=False)


def split_text(text: str, size: int) -> Iterable[str]:
    for index in range(0, len(text), size): yield text[index:index + size]
