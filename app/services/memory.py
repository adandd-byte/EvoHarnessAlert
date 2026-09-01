from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

from redis import Redis
from redis.exceptions import RedisError

from app.core.config import Settings, get_settings
from app.services.privacy import PrivacySanitizer


MEMORY_COMPACT_RECENT_MESSAGES = 8
MEMORY_SUMMARY_MAX_CHARS = 500
MEMORY_SUMMARY_USER_MESSAGES = 4
MEMORY_SUMMARY_ASSISTANT_MESSAGES = 3
MEMORY_SUMMARY_USER_CHARS = 80
MEMORY_SUMMARY_ASSISTANT_CHARS = 70
MEMORY_CURRENT_INPUT_CHARS = 80


@dataclass(frozen=True)
class ShortTermMessage:
    role: str
    content: str
    created_at: str

    def model_dump(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content, "createdAt": self.created_at}


class RedisShortTermMemoryStore:
    """
    Redis 短期记忆存储。

    只保存最近 N 条脱敏后的对话/告警研判消息，用于下一轮上下文恢复。
    长期事实、审计和报告仍以 MySQL 为准。
    """

    def __init__(self, settings: Settings | None = None, redis_client: Redis | None = None):
        self.settings = settings or get_settings()
        self.redis = redis_client or Redis.from_url(
            self.settings.redis_url,
            decode_responses=True,
            socket_timeout=self.settings.redis_socket_timeout_seconds,
        )
        self.sanitizer = PrivacySanitizer()

    def load_recent(self, owner: str, session_id: str, limit: int | None = None) -> list[ShortTermMessage]:
        limit = limit or self.settings.redis_memory_max_messages
        try:
            rows = self.redis.lrange(self._key(owner, session_id), max(0, -limit), -1)
        except RedisError:
            return []
        messages = []
        for row in rows:
            try:
                item = json.loads(row)
            except json.JSONDecodeError:
                continue
            role = str(item.get("role") or "user")
            content = self.sanitizer.sanitize(str(item.get("content") or ""))
            created_at = str(item.get("createdAt") or item.get("created_at") or "")
            messages.append(ShortTermMessage(role=role, content=content, created_at=created_at))
        return messages

    def append(self, owner: str, session_id: str, role: str, content: str, created_at: datetime | None = None) -> None:
        message = ShortTermMessage(
            role=role,
            content=self.sanitizer.sanitize(content),
            created_at=(created_at or datetime.utcnow()).isoformat(),
        )
        key = self._key(owner, session_id)
        try:
            self.redis.rpush(key, json.dumps(message.model_dump(), ensure_ascii=False))
            self.redis.ltrim(key, -self.settings.redis_memory_max_messages, -1)
            self.redis.expire(key, self.settings.redis_memory_ttl_seconds)
        except RedisError:
            return

    def replace(self, owner: str, session_id: str, messages: Iterable[ShortTermMessage | dict[str, Any]]) -> None:
        key = self._key(owner, session_id)
        rows = [
            json.dumps(self._normalize_message(message).model_dump(), ensure_ascii=False)
            for message in messages
        ][-self.settings.redis_memory_max_messages:]
        try:
            pipe = self.redis.pipeline()
            pipe.delete(key)
            if rows:
                pipe.rpush(key, *rows)
            pipe.expire(key, self.settings.redis_memory_ttl_seconds)
            pipe.execute()
        except RedisError:
            return

    def messages_from_owner_roles(self, owner: str, session_id: str, roles: set[str] | list[str]) -> list[ShortTermMessage]:
        allowed = {role.lower() for role in roles}
        return [message for message in self.load_recent(owner, session_id) if message.role.lower() in allowed]

    def _normalize_message(self, message: ShortTermMessage | dict[str, Any]) -> ShortTermMessage:
        if isinstance(message, ShortTermMessage):
            return ShortTermMessage(
                role=message.role,
                content=self.sanitizer.sanitize(message.content),
                created_at=message.created_at,
            )
        return ShortTermMessage(
            role=str(message.get("role") or "user"),
            content=self.sanitizer.sanitize(str(message.get("content") or "")),
            created_at=str(message.get("createdAt") or message.get("created_at") or datetime.utcnow().isoformat()),
        )

    @staticmethod
    def _key(owner: str, session_id: str) -> str:
        return f"evoharness:alert:memory:{owner}:{session_id}"


def status() -> dict:
    return {"status": "READY", "domain": "alerting", "message": "Redis 短期记忆模块已就绪"}


def compact_history_for_prompt(
    messages: list[ShortTermMessage | dict[str, Any]],
    current_input: str = "",
    recent_limit: int = MEMORY_COMPACT_RECENT_MESSAGES,
) -> list[dict[str, str]]:
    sanitizer = PrivacySanitizer()
    normalized = [_normalize_any_message(message, sanitizer) for message in messages]
    if len(normalized) <= recent_limit:
        return [message.model_dump() for message in normalized]

    summary = summarize_history_for_memory(normalized, current_input)
    system_message = ShortTermMessage(
        role="system",
        content=(
            "历史摘要仅供 EvoHarnessAlert 内部上下文使用，不要向值班人员或客户展示；"
            "不要据此输出后台标签、风险等级或未经验证的根因。"
            f"\n{summary}"
        ),
        created_at=datetime.utcnow().isoformat(),
    )
    recent = normalized[-recent_limit:]
    return [system_message.model_dump(), *[message.model_dump() for message in recent]]


def summarize_history_for_memory(
    messages: list[ShortTermMessage | dict[str, Any]],
    current_input: str = "",
) -> str:
    sanitizer = PrivacySanitizer()
    normalized = [_normalize_any_message(message, sanitizer) for message in messages]
    user_messages = [
        _clip(message.content, MEMORY_SUMMARY_USER_CHARS)
        for message in normalized
        if message.role == "user"
    ][-MEMORY_SUMMARY_USER_MESSAGES:]
    assistant_messages = [
        _clip(message.content, MEMORY_SUMMARY_ASSISTANT_CHARS)
        for message in normalized
        if message.role == "assistant"
    ][-MEMORY_SUMMARY_ASSISTANT_MESSAGES:]
    parts = []
    if user_messages:
        parts.append("近期关注：" + "；".join(user_messages))
    if assistant_messages:
        parts.append("已给建议：" + "；".join(assistant_messages))
    if current_input:
        parts.append("本轮输入：" + _clip(sanitizer.sanitize(current_input), MEMORY_CURRENT_INPUT_CHARS))
    if not parts:
        parts.append("暂无可用历史摘要。")
    return _clip("。".join(parts), MEMORY_SUMMARY_MAX_CHARS)


def _normalize_any_message(message: ShortTermMessage | dict[str, Any], sanitizer: PrivacySanitizer) -> ShortTermMessage:
    if isinstance(message, ShortTermMessage):
        return ShortTermMessage(
            role=message.role,
            content=sanitizer.sanitize(message.content),
            created_at=message.created_at,
        )
    return ShortTermMessage(
        role=str(message.get("role") or "user"),
        content=sanitizer.sanitize(str(message.get("content") or "")),
        created_at=str(message.get("createdAt") or message.get("created_at") or datetime.utcnow().isoformat()),
    )


def _clip(value: str, limit: int) -> str:
    value = " ".join((value or "").split())
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)] + "…"
