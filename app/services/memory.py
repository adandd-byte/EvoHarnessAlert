from __future__ import annotations

import json  # JSON 编解码
from dataclasses import dataclass  # 数据类
from datetime import datetime  # 时间
from typing import Any, Iterable  # 类型标注

from redis import Redis  # Redis 客户端
from redis.exceptions import RedisError  # Redis 异常

from app.core.config import Settings, get_settings  # 配置
from app.services.privacy import PrivacySanitizer  # 脱敏器


MEMORY_COMPACT_RECENT_MESSAGES = 8  # 保留最近消息条数
MEMORY_SUMMARY_MAX_CHARS = 500  # 历史摘要最大字符数
MEMORY_SUMMARY_USER_MESSAGES = 4  # 摘要里保留的用户消息条数
MEMORY_SUMMARY_ASSISTANT_MESSAGES = 3  # 摘要里保留的助手消息条数
MEMORY_SUMMARY_USER_CHARS = 80  # 单条用户消息摘要长度
MEMORY_SUMMARY_ASSISTANT_CHARS = 70  # 单条助手消息摘要长度
MEMORY_CURRENT_INPUT_CHARS = 80  # 本轮输入摘要长度


@dataclass(frozen=True)
class ShortTermMessage:
    role: str          # 角色（user/assistant/system）
    content: str       # 消息内容
    created_at: str    # 创建时间

    def model_dump(self) -> dict[str, str]:
        # 转成可序列化字典
        return {"role": self.role, "content": self.content, "createdAt": self.created_at}


class RedisShortTermMemoryStore:
    """
    Redis 短期记忆存储。

    只保存最近 N 条脱敏后的对话/告警研判消息，用于下一轮上下文恢复。
    长期事实、审计和报告仍以 MySQL 为准。
    """

    def __init__(self, settings: Settings | None = None, redis_client: Redis | None = None):
        self.settings = settings or get_settings()  # 全局配置
        self.redis = redis_client or Redis.from_url(  # Redis 客户端
            self.settings.redis_url,  # 连接地址
            decode_responses=True,  # 返回字符串
            socket_timeout=self.settings.redis_socket_timeout_seconds,  # 超时
        )
        self.sanitizer = PrivacySanitizer()  # 脱敏器

    def load_recent(self, owner: str, session_id: str, limit: int | None = None) -> list[ShortTermMessage]:
        # 读取最近 N 条消息（从尾部向前取）
        limit = self.settings.redis_memory_max_messages if limit is None else limit
        if limit <= 0:
            return []
        try:  # 尝试读取 Redis
            rows = self.redis.lrange(self._key(owner, session_id), -limit, -1)
        except RedisError:  # Redis 异常返回空
            return []
        messages = []
        for row in rows:  # 逐条解析
            try:
                item = json.loads(row)  # 反序列化
            except json.JSONDecodeError:
                continue  # 坏数据跳过
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "user")  # 角色
            content = self.sanitizer.sanitize(str(item.get("content") or ""))  # 内容脱敏
            created_at = str(item.get("createdAt") or item.get("created_at") or "")  # 时间
            messages.append(ShortTermMessage(role=role, content=content, created_at=created_at))
        return messages

    def append(self, owner: str, session_id: str, role: str, content: str, created_at: datetime | None = None) -> None:
        # 追加一条消息并裁剪到最大条数、刷新 TTL
        message = ShortTermMessage(  # 构造消息
            role=role,  # 角色
            content=self.sanitizer.sanitize(content),  # 脱敏内容
            created_at=(created_at or datetime.utcnow()).isoformat(),  # 时间
        )
        key = self._key(owner, session_id)  # Redis key
        try:  # 写入 Redis
            self.redis.rpush(key, json.dumps(message.model_dump(), ensure_ascii=False))  # 尾部追加
            self.redis.ltrim(key, -self.settings.redis_memory_max_messages, -1)  # 裁剪到上限
            self.redis.expire(key, self.settings.redis_memory_ttl_seconds)  # 刷新过期时间
        except RedisError:
            return  # 失败静默

    def replace(self, owner: str, session_id: str, messages: Iterable[ShortTermMessage | dict[str, Any]]) -> None:
        # 整体替换某会话的记忆（先删后写）
        key = self._key(owner, session_id)  # Redis key
        rows = [  # 序列化并裁剪
            json.dumps(self._normalize_message(message).model_dump(), ensure_ascii=False)  # 归一化后序列化
            for message in messages
        ][-self.settings.redis_memory_max_messages:]
        try:  # 管道原子执行
            pipe = self.redis.pipeline()  # 建管道
            pipe.delete(key)  # 删除旧数据
            if rows:  # 有新数据才写
                pipe.rpush(key, *rows)
            pipe.expire(key, self.settings.redis_memory_ttl_seconds)  # 设 TTL
            pipe.execute()
        except RedisError:
            return  # 失败静默

    def messages_from_owner_roles(self, owner: str, session_id: str, roles: set[str] | list[str]) -> list[ShortTermMessage]:
        # 按角色过滤最近消息
        allowed = {role.lower() for role in roles}  # 允许的角色小写集合
        return [message for message in self.load_recent(owner, session_id) if message.role.lower() in allowed]  # 过滤

    def _normalize_message(self, message: ShortTermMessage | dict[str, Any]) -> ShortTermMessage:
        # 把 ShortTermMessage 或字典统一归一化为 ShortTermMessage
        if isinstance(message, ShortTermMessage):  # 已是对象则重新脱敏
            return ShortTermMessage(
                role=message.role,  # 角色
                content=self.sanitizer.sanitize(message.content),  # 脱敏
                created_at=message.created_at,
            )
        return ShortTermMessage(  # 字典转对象
            role=str(message.get("role") or "user"),  # 角色
            content=self.sanitizer.sanitize(str(message.get("content") or "")),  # 脱敏
            created_at=str(message.get("createdAt") or message.get("created_at") or datetime.utcnow().isoformat()),
        )

    @staticmethod
    def _key(owner: str, session_id: str) -> str:
        # 生成 Redis key
        return f"evoharness:alert:memory:{owner}:{session_id}"


def status() -> dict:
    return {"status": "READY", "domain": "alerting", "message": "Redis 短期记忆模块已就绪"}


def compact_history_for_prompt(
    messages: list[ShortTermMessage | dict[str, Any]],
    current_input: str = "",
    recent_limit: int = MEMORY_COMPACT_RECENT_MESSAGES,
) -> list[dict[str, str]]:
    # 压缩历史用于拼 prompt：消息少就直接返回，多了则用摘要+最近消息
    sanitizer = PrivacySanitizer()  # 脱敏器
    normalized = [_normalize_any_message(message, sanitizer) for message in messages]  # 归一化
    if len(normalized) <= recent_limit:  # 未超阈值直接返回
        return [message.model_dump() for message in normalized]

    summary = summarize_history_for_memory(normalized, current_input)  # 生成历史摘要
    system_message = ShortTermMessage(  # 构造摘要系统消息
        role="system",  # 系统角色
        content=(  # 拼接摘要内容
            "历史摘要仅供 EvoHarnessAlert 内部上下文使用，不要向值班人员或客户展示；"
            "不要据此输出后台标签、风险等级或未经验证的根因。"
            f"\n{summary}"
        ),
        created_at=datetime.utcnow().isoformat(),  # 时间
    )
    recent = normalized[-recent_limit:]  # 取最近的原始消息
    return [system_message.model_dump(), *[message.model_dump() for message in recent]]  # 摘要+最近消息


def summarize_history_for_memory(
    messages: list[ShortTermMessage | dict[str, Any]],
    current_input: str = "",
) -> str:
    # 生成记忆摘要：分别抽取用户关注与已给建议
    sanitizer = PrivacySanitizer()  # 脱敏器
    normalized = [_normalize_any_message(message, sanitizer) for message in messages]  # 归一化
    user_messages = [  # 用户最近若干条
        _clip(message.content, MEMORY_SUMMARY_USER_CHARS)  # 截断长度
        for message in normalized
        if message.role == "user"
    ][-MEMORY_SUMMARY_USER_MESSAGES:]
    assistant_messages = [  # 助手最近若干条
        _clip(message.content, MEMORY_SUMMARY_ASSISTANT_CHARS)  # 截断长度
        for message in normalized
        if message.role == "assistant"
    ][-MEMORY_SUMMARY_ASSISTANT_MESSAGES:]
    parts = []  # 摘要片段
    if user_messages:  # 有用户消息则加关注
        parts.append("近期关注：" + "；".join(user_messages))
    if assistant_messages:  # 有助手消息则加建议
        parts.append("已给建议：" + "；".join(assistant_messages))
    if current_input:  # 有本轮输入则追加
        parts.append("本轮输入：" + _clip(sanitizer.sanitize(current_input), MEMORY_CURRENT_INPUT_CHARS))
    if not parts:  # 都没有则占位
        parts.append("暂无可用历史摘要。")
    return _clip("。".join(parts), MEMORY_SUMMARY_MAX_CHARS)  # 拼接并截断


def _normalize_any_message(message: ShortTermMessage | dict[str, Any], sanitizer: PrivacySanitizer) -> ShortTermMessage:
    # 工具函数：把消息归一化为 ShortTermMessage 并脱敏
    if isinstance(message, ShortTermMessage):  # 对象分支
        return ShortTermMessage(
            role=message.role,  # 角色
            content=sanitizer.sanitize(message.content),  # 脱敏
            created_at=message.created_at,
        )
    return ShortTermMessage(  # 字典分支
        role=str(message.get("role") or "user"),  # 角色
        content=sanitizer.sanitize(str(message.get("content") or "")),  # 脱敏
        created_at=str(message.get("createdAt") or message.get("created_at") or datetime.utcnow().isoformat()),
    )


def _clip(value: str, limit: int) -> str:
    # 压缩空白并截断到指定长度
    value = " ".join((value or "").split())  # 合并连续空白
    if len(value) <= limit:  # 未超长度原样返回
        return value
    return value[: max(0, limit - 1)] + "…"  # 截断并加省略号
