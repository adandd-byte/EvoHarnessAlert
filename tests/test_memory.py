from __future__ import annotations

import json
from unittest.mock import Mock

import pytest
from redis.exceptions import RedisError

from app.core.config import Settings
from app.services.memory import RedisShortTermMemoryStore, ShortTermMessage, compact_history_for_prompt, summarize_history_for_memory


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


def test_load_recent_returns_only_requested_tail():
    store = RedisShortTermMemoryStore(Settings(_env_file=None), FakeRedis())
    for index in range(5):
        store.append("team", "session", "user", f"消息{index}")
    assert [item.content for item in store.load_recent("team", "session", limit=2)] == ["消息3", "消息4"]


@pytest.mark.parametrize("limit", [0, -1])
def test_nonpositive_memory_limit_returns_no_messages(limit):
    redis = Mock()
    store = RedisShortTermMemoryStore(Settings(_env_file=None), redis)
    assert store.load_recent("team", "session", limit=limit) == []
    redis.lrange.assert_not_called()


def test_cache_reads_skip_corrupt_rows_and_sanitize_again():
    redis = FakeRedis()
    store = RedisShortTermMemoryStore(Settings(_env_file=None), redis)
    redis.rpush(store._key("team", "session"), "broken-json", "null", "[]", json.dumps({"role": "user", "content": "token=secret"}))
    messages = store.load_recent("team", "session")
    assert len(messages) == 1
    assert "secret" not in messages[0].content


def test_memory_owner_and_session_are_isolated():
    store = RedisShortTermMemoryStore(Settings(_env_file=None), FakeRedis())
    store.append("team-a", "session-1", "user", "团购故障")
    assert store.load_recent("team-b", "session-1") == []
    assert store.load_recent("team-a", "session-2") == []


@pytest.mark.parametrize("method", ["load_recent", "append", "replace"])
def test_redis_outage_degrades_without_raising(method):
    redis = Mock()
    redis.lrange.side_effect = RedisError("断连")
    redis.rpush.side_effect = RedisError("断连")
    redis.pipeline.side_effect = RedisError("断连")
    store = RedisShortTermMemoryStore(Settings(_env_file=None), redis)
    args = {"load_recent": (), "append": ("user", "告警"), "replace": ([],)}
    result = getattr(store, method)("team", "session", *args[method])
    assert result == ([] if method == "load_recent" else None)


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


def test_compact_history_keeps_short_history_as_sanitized_messages():
    messages = [
        {"role": "user", "content": "P1 告警 token=abc"},
        {"role": "assistant", "content": "先查日志"},
    ]

    compacted = compact_history_for_prompt(messages)

    assert len(compacted) == 2
    assert compacted[0]["role"] == "user"
    assert "abc" not in str(compacted)


def test_compact_history_summarizes_long_history_and_keeps_recent_eight():
    messages = [
        ShortTermMessage(role="user" if index % 2 == 0 else "assistant", content=f"第{index}条消息，手机号 13812345678，关于 coupon-service 告警排查", created_at="2026-09-01T12:00:00")
        for index in range(12)
    ]

    compacted = compact_history_for_prompt(messages, current_input="本轮是 EVENT 发布告警，不要被旧 PROBLEM 干扰")

    assert len(compacted) == 9
    assert compacted[0]["role"] == "system"
    assert "内部上下文" in compacted[0]["content"]
    assert "风险等级" in compacted[0]["content"]
    assert "13812345678" not in str(compacted)
    assert compacted[-1]["content"].startswith("第11条消息")


def test_summarize_history_for_memory_is_deterministic_and_bounded():
    messages = [
        {"role": "user", "content": "用户持续补充 coupon-service 核销失败上下文 " * 20},
        {"role": "assistant", "content": "建议查询日志、Trace、代码和大盘 " * 20},
    ]

    summary = summarize_history_for_memory(messages, current_input="当前输入包含 password=secret")

    assert len(summary) <= 500
    assert "secret" not in summary
    assert "近期关注" in summary
    assert "已给建议" in summary
