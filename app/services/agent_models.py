"""Agent 模型注册表：给每个 Agent 分配独立的模型配置。

参考 mindbridge-py 的 app/services/agent_models.py 移植。

设计动机：
- 不同 Agent 的任务性质不同，应该允许用不同的模型/温度/长度，例如：
  - ResponseAgent 生成研判报告：可以用温度稍高、长度更大的配置；
  - SafetyAgent 安全审查：应该用低温、保守的配置；
- 所有配置都遵循 `agent_model_{别名}_{字段}` 的命名约定，可以只在
  .env 里覆盖想定制的那几项，其余自动回落到全局默认。

可用配置项示例：
    AGENT_MODEL_DEFAULT_PROVIDER=openai     # 所有 Agent 的默认 provider
    AGENT_MODEL_DEFAULT_MODEL=kimi-k2       # 所有 Agent 的默认模型
    AGENT_MODEL_SAFETY_TEMPERATURE=0.0      # 只覆盖 SafetyAgent 的温度
    AGENT_MODEL_RESPONSE_MAX_TOKENS=2048    # 只覆盖 ResponseAgent 的长度
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from app.core.config import Settings
from app.services.ai import AiClient


# Agent 名称 -> 配置别名。新增 Agent 时在这里补一行即可。
AGENT_MODEL_ALIASES = {
    "CoordinatorAgent": "coordinator",
    "ContextAgent": "context",
    "ResponseAgent": "response",
    "SafetyAgent": "safety",
}


@dataclass(frozen=True)
class AgentModelProfile:
    """单个 Agent 的模型画像：用哪个 provider 的哪个模型、什么温度、多长输出。"""

    provider: str      # mock / ollama / openai
    model: str         # 模型名，如 kimi-k2、evoharness-alert-qwen2.5-7b:latest
    temperature: float # 采样温度：越低越确定，安全审查建议 0
    max_tokens: int    # 最大输出长度


class AgentModelRegistry:
    """按 Agent 名称解析模型画像并构造对应的 AiClient。"""

    def __init__(self, settings: Settings):
        self.settings = settings

    def profile_for(self, agent_name: str) -> AgentModelProfile:
        """解析某个 Agent 的模型画像；未配置的字段逐级回落到全局默认。"""
        alias = AGENT_MODEL_ALIASES.get(agent_name, _snake(agent_name.removesuffix("Agent")))
        provider = self._setting(f"agent_model_{alias}_provider", self._default_provider())
        model = self._setting(f"agent_model_{alias}_model", self._default_model(provider))
        temperature = float(self._setting(f"agent_model_{alias}_temperature", getattr(self.settings, "ai_temperature", 0.35)))
        max_tokens = int(self._setting(f"agent_model_{alias}_max_tokens", getattr(self.settings, "ai_max_tokens", 512)))
        return AgentModelProfile(provider=provider, model=model, temperature=temperature, max_tokens=max_tokens)

    def client_for(self, agent_name: str) -> AiClient:
        """为某个 Agent 构造专属 AiClient：复制全局配置后覆盖画像字段。"""
        profile = self.profile_for(agent_name)
        settings = copy.copy(self.settings)
        settings.ai_provider = profile.provider
        settings.ai_temperature = profile.temperature
        settings.ai_max_tokens = profile.max_tokens
        if profile.provider == "openai":
            settings.openai_model = profile.model
        else:
            settings.ollama_model = profile.model
        return AiClient(settings)

    # ---------------------------------------------------------------- 内部工具

    def _setting(self, name: str, fallback: Any) -> Any:
        """读 settings 上的动态配置项；空值一律回落，避免空字符串覆盖默认。"""
        value = getattr(self.settings, name, None)
        if value in {None, ""}:
            return fallback
        return value

    def _default_provider(self) -> str:
        return str(self._setting("agent_model_default_provider", getattr(self.settings, "ai_provider", "mock"))).lower()

    def _default_model(self, provider: str) -> str:
        configured = self._setting("agent_model_default_model", "")
        if configured:
            return str(configured)
        if provider == "openai":
            return getattr(self.settings, "openai_model", "gpt-4o-mini")
        if provider == "ollama":
            return getattr(self.settings, "ollama_model", "evoharness-alert-qwen2.5-7b:latest")
        return "mock"


def _snake(value: str) -> str:
    """驼峰转下划线：TriggerAgent -> trigger，供未登记别名的 Agent 兜底。"""
    chars = []
    for index, char in enumerate(value):
        if char.isupper() and index > 0:
            chars.append("_")
        chars.append(char.lower())
    return "".join(chars)
