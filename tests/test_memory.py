from __future__ import annotations

from app.core.config import Settings
from app.services.memory import RedisShortTermMemoryStore


class FakeRedis:
    def __init__(self):
        self.data = {}
        self.expirations = {}

    def lrange(self, key, start, end):
        values = self.data.get(key, [])
        if start < 0:
            start = max(0, len(values) + start)
        if end < 0:
            end = len(values) + end
        return values[start:end + 1]

    def rpush(self, key, *values):
        self.data.setdefault(key, []).extend(values)

    def ltrim(self, key, start, end):
        values = self.data.get(key, [])
        if start < 0:
            start = max(0, len(values) + start)
        if end < 0:
            end = len(values) + end
        self.data[key] = values[start:end + 1]

    def expire(self, key, seconds):
        self.expirations[key] = seconds

    def delete(self, key):
        self.data.pop(key, None)

    def pipeline(self):
        return self

    def execute(self):
        return True


def test_redis_short_term_memory_append_trims_and_expires():
    settings = Settings(redis_memory_max_messages=2, redis_memory_ttl_seconds=86400)
    redis = FakeRedis()
    store = RedisShortTermMemoryStore(settings, redis)

    store.append("team-a", "session-1", "user", "第一条")
    store.append("team-a", "session-1", "assistant", "第二条")
    store.append("team-a", "session-1", "user", "手机号 13812345678 token=abc")

    key = "evoharness:alert:memory:team-a:session-1"
    recent = store.load_recent("team-a", "session-1")
    rendered = " ".join(message.content for message in recent)
    assert len(recent) == 2
    assert "第一条" not in rendered
    assert "13812345678" not in rendered
    assert "abc" not in rendered
    assert "[已脱敏]" in rendered
    assert redis.expirations[key] == 86400


def test_redis_short_term_memory_replace_and_filter_roles():
    settings = Settings(redis_memory_max_messages=40)
    store = RedisShortTermMemoryStore(settings, FakeRedis())

    store.replace(
        "team-a",
        "session-2",
        [
            {"role": "system", "content": "系统消息"},
            {"role": "user", "content": "用户消息"},
            {"role": "assistant", "content": "助手消息"},
        ],
    )

    messages = store.messages_from_owner_roles("team-a", "session-2", {"user", "assistant"})
    assert [message.role for message in messages] == ["user", "assistant"]
