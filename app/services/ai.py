from __future__ import annotations

import json  # JSON 编解码
from typing import Iterable  # 可迭代对象

import httpx  # HTTP 客户端

from app.core.config import Settings  # 全局配置
from app.schemas.dtos import AiMessage  # 对话消息 DTO


class PromptTemplates:
    @staticmethod
    def alert_assessment(payload: str) -> list[AiMessage]:
        # 告警研判的固定提示模板：要求模型只返回 JSON 判断题干字段
        return [AiMessage(role="system", content="你是 EvoHarnessAlert 告警助手。只返回 JSON，字段包含 summary、impact、rootCauseHint、runbookHint。"), AiMessage(role="user", content=payload)]


class AiClient:
    # AI 客户端：按配置 provider 分发到 ollama / openai / mock
    def __init__(self, settings: Settings): self.settings = settings
    def complete(self, messages: list[AiMessage]) -> str:
        provider = self.settings.ai_provider.lower()  # 取 provider 并转小写
        if provider == "ollama": return self._ollama(messages)  # 本地 Ollama
        if provider == "openai": return self._openai(messages)  # 远程 OpenAI 兼容
        return self._mock(messages)  # 测试用 mock
    async def stream(self, messages: list[AiMessage]):
        # 流式输出：对完整回复按 chunk 切分后逐个产出
        for chunk in split_text(self.complete(messages), 24): yield chunk
    def _ollama(self, messages: list[AiMessage]) -> str:
        # 调用本地 Ollama 的 /api/chat 接口
        response = httpx.post(f"{self.settings.ollama_base_url}/api/chat", json={"model": self.settings.ollama_model, "messages": [m.model_dump() for m in messages], "stream": False}, timeout=60); response.raise_for_status(); return response.json()["message"]["content"]
    def _openai(self, messages: list[AiMessage]) -> str:
        headers = {**self.settings.openai_extra_headers, "Authorization": f"Bearer {self.settings.openai_api_key}"}
        base = self.settings.openai_base_url.rstrip("/")
        if self.settings.openai_wire_api == "responses":
            if self.settings.openai_responses_stream:
                return self._responses_stream(messages, base, headers)
            response = httpx.post(
                f"{base}/responses", headers=headers,
                json={"model": self.settings.openai_model, "input": [{"role": m.role, "content": [{"type": "input_text", "text": m.content}]} for m in messages],
                      "max_output_tokens": self.settings.ai_max_tokens, "stream": False, "store": False},
                timeout=60, trust_env=self.settings.ai_trust_env,
            )
            response.raise_for_status()
            data = response.json()
            if data.get("status") != "completed":
                raise ValueError("Model response did not complete")
            text = "".join(part.get("text", "") for item in data.get("output", [])
                           if item.get("type") == "message" for part in item.get("content", [])
                           if part.get("type") == "output_text")
            if not text.strip():
                raise ValueError("Model returned empty text")
            return text
        # 调用 OpenAI 兼容的 /chat/completions 接口（可指向 Moonshot/Kimi 等）
        response = httpx.post(f"{self.settings.openai_base_url}/chat/completions", headers={"Authorization": f"Bearer {self.settings.openai_api_key}"}, json={"model": self.settings.openai_model, "messages": [m.model_dump() for m in messages], "temperature": self.settings.ai_temperature, "max_tokens": self.settings.ai_max_tokens}, timeout=60); response.raise_for_status(); return response.json()["choices"][0]["message"]["content"]
    def _responses_stream(self, messages, base, headers) -> str:
        chunks = []
        with httpx.Client(timeout=60, trust_env=self.settings.ai_trust_env) as client:
            with client.stream("POST", f"{base}/responses", headers=headers, json={
                "model": self.settings.openai_model,
                "input": [{"role": m.role, "content": [{"type": "input_text", "text": m.content}]} for m in messages],
                "max_output_tokens": self.settings.ai_max_tokens, "stream": True, "store": False,
            }) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if raw == "[DONE]":
                        break
                    event = json.loads(raw)
                    kind = event.get("type")
                    if kind == "response.output_text.delta":
                        chunks.append(event.get("delta", ""))
                    elif kind in {"response.failed", "response.incomplete", "error"}:
                        raise ValueError("Model stream failed or was incomplete")
                    elif kind == "response.completed":
                        text = "".join(chunks)
                        if not text.strip():
                            raise ValueError("Model stream returned empty text")
                        return text
        raise ValueError("Model stream ended without completion")

    def _mock(self, messages: list[AiMessage]) -> str:
        # mock 模式：不调模型，返回固定 JSON 研判占位
        return json.dumps({"summary": "告警已完成基础研判", "impact": "请结合指标和日志确认影响范围", "rootCauseHint": "优先检查最近发布、依赖状态和资源水位", "runbookHint": "先止血，再定位；必要时升级给值班负责人"}, ensure_ascii=False)


def split_text(text: str, size: int) -> Iterable[str]:
    # 按固定长度切分文本，用于模拟流式输出
    for index in range(0, len(text), size): yield text[index:index + size]
