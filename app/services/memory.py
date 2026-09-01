from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

from redis import Redis
from redis.exceptions import RedisError

from app.core.config import Settings, get_settings
from app.services.privacy import PrivacySanitizer


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
