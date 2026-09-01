from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def now() -> datetime:
    return datetime.utcnow()


class UserAccount(Base):
    """
    用户账号表。

    用于保存系统登录用户、密码哈希以及角色权限信息。
    """

    __tablename__ = "user_accounts"

    # 用户主键 ID。
    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    # 登录用户名。
    # unique=True 保证用户名不能重复。
    # index=True 便于登录时快速查询。
    username: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        index=True,
    )

    # 用户展示名称。
    display_name: Mapped[str] = mapped_column(
        String(128),
    )

    # 密码哈希值。
    # 注意：数据库中不应该保存明文密码。
    password_hash: Mapped[str] = mapped_column(
        String(128),
    )

    # 用户角色，以逗号分隔字符串保存。
    #
    # 例如：
    # ROLE_ADMIN,ROLE_OPERATOR
    #
    # 默认拥有管理员角色。
    roles_csv: Mapped[str] = mapped_column(
        String(256),
        default="ROLE_ADMIN",
    )

    # 用户创建时间。
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=now,
    )

    @property
    def roles(self) -> list[str]:
        """
        将 roles_csv 转换成角色列表。

        例如：
        "ROLE_ADMIN,ROLE_OPERATOR"

        转换为：
        ["ROLE_ADMIN", "ROLE_OPERATOR"]
        """

        return [
            role
            for role in self.roles_csv.split(",")
            if role
        ]

    @roles.setter
    def roles(
        self,
        value: list[str] | set[str],
    ) -> None:
        """
        设置用户角色。

        输入 list/set 后：
        1. 对角色排序。
        2. 使用逗号连接。
        3. 保存到 roles_csv。
        """

        self.roles_csv = ",".join(
            sorted(value)
        )


class AlertSource(Base):
    """
    告警来源表。

    用于记录告警来自哪个外部系统，例如：
    - Prometheus
    - Grafana
    - CloudWatch
    - 自定义 Webhook
    """

    __tablename__ = "alert_sources"

    # 告警来源主键。
    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    # 来源名称。
    # 全局唯一，例如 prometheus。
    name: Mapped[str] = mapped_column(
        String(128),
        unique=True,
        index=True,
    )

    # 来源类型。
    # 默认按照 webhook 类型处理。
    kind: Mapped[str] = mapped_column(
        String(64),
        default="webhook",
    )

    # 来源描述。
    description: Mapped[str] = mapped_column(
        Text,
        default="",
    )

    # 来源创建时间。
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=now,
    )


class AlertEvent(Base):
    """
    原始告警事件表。

    每收到一条外部告警，通常都会创建一条 AlertEvent。

    AlertEvent 更偏向“事实记录”：
    保存原始告警、标签、状态、严重等级以及原始 Payload。
    """

    __tablename__ = "alert_events"

    # 告警事件主键。
    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    # 告警来源 ID。
    # 关联 alert_sources.id。
    source_id: Mapped[int] = mapped_column(
        ForeignKey("alert_sources.id"),
        index=True,
    )

    # 外部告警系统中的告警 ID。
    external_id: Mapped[str] = mapped_column(
        String(256),
        default="",
        index=True,
    )

    # 告警指纹。
    #
    # 通常由来源、标题、labels 等字段计算，
    # 用于：
    # - 去重
    # - 告警关联
    # - Incident 聚合
    fingerprint: Mapped[str] = mapped_column(
        String(256),
        index=True,
    )

    # 告警标题。
    title: Mapped[str] = mapped_column(
        String(256),
    )

    # 告警详细描述。
    description: Mapped[str] = mapped_column(
        Text,
        default="",
    )

    # 告警标签 JSON。
    #
    # 例如：
    # {
    #     "service": "payment",
    #     "instance": "10.0.0.1",
    #     "severity": "P1"
    # }
    labels_json: Mapped[str] = mapped_column(
        Text,
        default="{}",
    )

    # 告警 annotations JSON。
    #
    # 一般用于保存 summary、description、
    # runbook_url 等附加信息。
    annotations_json: Mapped[str] = mapped_column(
        Text,
        default="{}",
    )

    # 标准化后的告警优先级：P0 / P1 / P2 / P3。
    severity: Mapped[str] = mapped_column(
        String(32),
        index=True,
    )

    # 告警类型：PROBLEM / BUSINESS / EVENT / HOST。
    alert_type: Mapped[str] = mapped_column(
        String(32),
        default="PROBLEM",
        index=True,
    )

    # 告警状态。
    #
    # 例如：
    # FIRING
    # RESOLVED
    status: Mapped[str] = mapped_column(
        String(32),
        index=True,
    )

    # 告警开始时间。
    starts_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime,
        nullable=True,
    )

    # 告警结束/恢复时间。
    ends_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime,
        nullable=True,
    )

    # 保存外部系统提交的完整原始 Payload。
    #
    # 主要用于：
    # - 审计
    # - 调试
    # - 重新处理
    # - Agent 分析回放
    raw_payload_json: Mapped[str] = mapped_column(
        Text,
        default="{}",
    )

    # 告警接入系统的时间。
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=now,
        index=True,
    )

    # ORM 关系：
    # alert.source 可以直接访问 AlertSource。
    source: Mapped[AlertSource] = relationship()


class Incident(Base):
    """
    事件/故障表。

    多条相同或相关的 AlertEvent
    可以聚合成一个 Incident。

    AlertEvent 表示“发生了一条告警”，
    Incident 表示“当前正在处理的一个故障事件”。
    """

    __tablename__ = "incidents"

    # Incident 主键。
    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    # Incident 聚合键。
    #
    # 相同 incident_key 的告警会被聚合到同一个事件。
    incident_key: Mapped[str] = mapped_column(
        String(256),
        unique=True,
        index=True,
    )

    # 当前 Incident 的最高严重等级。
    severity: Mapped[str] = mapped_column(
        String(32),
        index=True,
    )

    # Incident 类型：PROBLEM / BUSINESS / EVENT / HOST。
    alert_type: Mapped[str] = mapped_column(
        String(32),
        default="PROBLEM",
        index=True,
    )

    # Incident 当前状态。
    #
    # 例如：
    # OPEN
    # ACKNOWLEDGED
    # RESOLVED
    status: Mapped[str] = mapped_column(
        String(32),
        index=True,
    )

    # 当前负责人。
    owner: Mapped[str] = mapped_column(
        String(128),
        default="未分派",
    )

    # Incident 摘要。
    summary: Mapped[str] = mapped_column(
        Text,
    )

    # 故障影响范围。
    impact: Mapped[str] = mapped_column(
        Text,
        default="",
    )

    # 潜在根因提示。
    root_cause_hint: Mapped[str] = mapped_column(
        Text,
        default="",
    )

    # 推荐 Runbook / 处置方案。
    runbook_hint: Mapped[str] = mapped_column(
        Text,
        default="",
    )

    # 当前 Incident 聚合了多少条告警。
    alert_count: Mapped[int] = mapped_column(
        Integer,
        default=1,
    )

    # 最近一次关联的 AlertEvent ID。
    last_alert_id: Mapped[int] = mapped_column(
        Integer,
        index=True,
    )

    # 确认/接手该事件的人员。
    acknowledged_by: Mapped[Optional[str]] = mapped_column(
        String(128),
        nullable=True,
    )

    # Incident 被确认的时间。
    acknowledged_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime,
        nullable=True,
    )

    # 关闭/解决 Incident 的人员。
    resolved_by: Mapped[Optional[str]] = mapped_column(
        String(128),
        nullable=True,
    )

    # Incident 被解决的时间。
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime,
        nullable=True,
    )

    # Incident 创建时间。
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=now,
    )

    # Incident 最近更新时间。
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=now,
        index=True,
    )


class IncidentNote(Base):
    """
    Incident 人工处置记录表。

    用于记录 Incident 的操作时间线，例如：
    - 谁接手了事件
    - 做了什么操作
    - 添加了什么备注
    - 为什么关闭事件
    """

    __tablename__ = "incident_notes"

    # 记录主键。
    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    # 关联 Incident。
    incident_id: Mapped[int] = mapped_column(
        ForeignKey("incidents.id"),
        index=True,
    )

    # 操作人员。
    actor: Mapped[str] = mapped_column(
        String(128),
    )

    # 具体操作或备注内容。
    note: Mapped[str] = mapped_column(
        Text,
    )

    # 记录创建时间。
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=now,
    )


class NotificationRecord(Base):
    """
    告警通知发送记录。

    用于记录：
    - 通知发送给谁
    - 使用什么渠道
    - 是否发送成功
    - 实际发送内容

    可以作为通知审计日志。
    """

    __tablename__ = "notification_records"

    # 主键。
    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    # 关联的 AlertEvent ID。
    alert_id: Mapped[int] = mapped_column(
        Integer,
        index=True,
    )

    # 关联 Incident ID。
    #
    # P3 告警可能没有 Incident，因此允许为空。
    incident_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        index=True,
    )

    # 通知渠道。
    #
    # 例如：
    # email
    # webhook
    # slack
    # sms
    channel: Mapped[str] = mapped_column(
        String(64),
    )

    # 通知接收方。
    recipient: Mapped[str] = mapped_column(
        String(256),
    )

    # 通知发送状态。
    #
    # 例如：
    # SUCCESS
    # FAILED
    status: Mapped[str] = mapped_column(
        String(32),
    )

    # 实际发送的消息内容。
    message: Mapped[str] = mapped_column(
        Text,
    )

    # 通知记录创建时间。
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=now,
    )


class LedgerRecord(Base):
    """
    告警台账写入记录。

    用于记录某条告警是否已经成功写入
    外部文件、Excel、CSV 或其他台账系统。
    """

    __tablename__ = "ledger_records"

    # 主键。
    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    # 对应 AlertEvent。
    alert_id: Mapped[int] = mapped_column(
        Integer,
        index=True,
    )

    # 台账文件路径。
    file_path: Mapped[str] = mapped_column(
        String(512),
    )

    # 写入状态。
    status: Mapped[str] = mapped_column(
        String(32),
    )

    # 写入结果或错误信息。
    message: Mapped[str] = mapped_column(
        Text,
    )

    # 创建时间。
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=now,
    )


class ToolJob(Base):
    """
    异步工具任务队列表。

    告警接入后不会直接执行所有外部操作，
    而是先创建 ToolJob，由 Worker 异步执行。

    例如：
    - LEDGER_WRITE
    - INCIDENT_UPSERT
    - NOTIFICATION_SEND
    """

    __tablename__ = "tool_jobs"

    # Job 主键。
    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    # 关联告警。
    alert_id: Mapped[int] = mapped_column(
        Integer,
        index=True,
    )

    # 关联 Incident。
    incident_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        index=True,
    )

    # Job 类型。
    kind: Mapped[str] = mapped_column(
        String(64),
        index=True,
    )

    # Job 当前状态。
    #
    # 例如：
    # PENDING
    # RUNNING
    # SUCCESS
    # FAILED
    status: Mapped[str] = mapped_column(
        String(32),
        index=True,
    )

    # 当前已经尝试执行的次数。
    attempts: Mapped[int] = mapped_column(
        Integer,
        default=0,
    )

    # 最大允许重试次数。
    max_attempts: Mapped[int] = mapped_column(
        Integer,
        default=3,
    )

    # 最早允许执行的时间。
    #
    # 可用于：
    # - 延迟执行
    # - 指数退避
    # - 失败重试
    run_after: Mapped[datetime] = mapped_column(
        DateTime,
        default=now,
        index=True,
    )

    # 最近一次执行失败的错误信息。
    last_error: Mapped[str] = mapped_column(
        Text,
        default="",
    )

    # Job 创建时间。
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=now,
    )

    # Job 最近更新时间。
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=now,
    )


class DeadLetterRecord(Base):
    """
    死信记录表。

    当 ToolJob 多次执行失败并超过最大重试次数后，
    可以将失败任务写入 Dead Letter Queue。

    用于：
    - 人工排查
    - 故障恢复
    - 后续重新执行
    """

    __tablename__ = "dead_letter_records"

    # 死信主键。
    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    # 原 ToolJob ID。
    #
    # 部分非 Job 类型失败记录可能没有 job_id。
    job_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        index=True,
    )

    # 对应告警 ID。
    alert_id: Mapped[int] = mapped_column(
        Integer,
        index=True,
    )

    # 对应 Incident。
    incident_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        index=True,
    )

    # 失败任务类型。
    kind: Mapped[str] = mapped_column(
        String(64),
        index=True,
    )

    # 最终失败原因。
    reason: Mapped[str] = mapped_column(
        Text,
    )

    # 保存失败任务相关 Payload。
    #
    # 便于后续人工重放。
    payload: Mapped[str] = mapped_column(
        Text,
        default="",
    )

    # 死信记录创建时间。
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=now,
    )


class AgentRunTrace(Base):
    """
    Agent 执行轨迹表。

    用于保存 AI / Agent 对告警的完整研判过程。

    包括：
    - 原始告警
    - Agent 执行步骤
    - 检索到的知识
    - 最终分析结果

    主要用于：
    - 可观测性
    - 审计
    - 调试
    - AI 分析回放
    """

    __tablename__ = "agent_run_traces"

    # Trace 主键。
    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    # 对应 AlertEvent。
    alert_id: Mapped[int] = mapped_column(
        Integer,
        index=True,
    )

    # 对应 Incident。
    #
    # P3 告警可能不存在 Incident。
    incident_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        index=True,
    )

    # 本次分析的告警严重等级。
    severity: Mapped[str] = mapped_column(
        String(32),
        index=True,
    )

    # 本次分析的告警类型。
    alert_type: Mapped[str] = mapped_column(
        String(32),
        default="PROBLEM",
        index=True,
    )

    # 原始告警 Payload。
    original_payload_json: Mapped[str] = mapped_column(
        Text,
        default="{}",
    )

    # Agent 执行步骤。
    #
    # 例如：
    # [
    #   {"agent": "TriageAgent", ...},
    #   {"agent": "ContextAgent", ...}
    # ]
    agent_steps_json: Mapped[str] = mapped_column(
        Text,
        default="[]",
    )

    # RAG / KnowledgeService
    # 检索到的知识内容及相关度。
    retrieved_knowledge_json: Mapped[str] = mapped_column(
        Text,
        default="[]",
    )

    # 最终告警分析结果。
    #
    # 例如：
    # {
    #   "summary": "...",
    #   "impact": "...",
    #   "rootCauseHint": "...",
    #   "runbookHint": "..."
    # }
    assessment_json: Mapped[str] = mapped_column(
        Text,
        default="{}",
    )

    # Trace 创建时间。
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=now,
    )


class ToolAuditRecord(Base):
    """
    Tool 调用审计记录。

    用于记录系统调用外部工具时：
    - 调用了什么工具
    - 使用了什么策略
    - 是否允许调用
    - 最终执行状态
    - 调用参数
    - 失败原因

    主要用于安全审计和可追溯性。
    """

    __tablename__ = "tool_audit_records"

    # 主键。
    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    # 对应 ToolJob。
    job_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        index=True,
    )

    # 对应 AlertEvent。
    alert_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        index=True,
    )

    # 被调用的工具名称。
    #
    # 例如：
    # notification
    # ledger
    # incident_api
    tool_name: Mapped[str] = mapped_column(
        String(64),
        index=True,
    )

    # Tool 调用时应用的安全策略。
    policy: Mapped[str] = mapped_column(
        String(128),
        default="",
    )

    # 当前策略是否允许执行。
    allowed: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
    )

    # Tool 实际执行状态。
    status: Mapped[str] = mapped_column(
        String(32),
        index=True,
    )

    # 拒绝执行或执行失败的原因。
    reason: Mapped[str] = mapped_column(
        Text,
        default="",
    )

    # Tool 调用 Payload。
    #
    # 主要用于审计，不建议保存密码、
    # Token 等敏感凭据。
    payload: Mapped[str] = mapped_column(
        Text,
        default="{}",
    )

    # 审计记录创建时间。
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=now,
    )

    # 审计记录更新时间。
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=now,
    )


class KnowledgeChunk(Base):
    """
    RAG 知识分块表。

    原始知识文档被拆分成多个 Chunk 后，
    每个 Chunk 保存为一条 KnowledgeChunk。

    KnowledgeService 检索时，
    会使用这些 Chunk 进行相关性搜索。
    """

    __tablename__ = "knowledge_chunks"

    # Chunk 主键。
    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    # Chunk 所属知识来源。
    #
    # 例如：
    # cpu-runbook.md
    # database-guide.md
    # 内置运维手册
    source: Mapped[str] = mapped_column(
        String(256),
        index=True,
    )

    # Chunk 在原始文档中的顺序。
    #
    # 例如一个文档被分成 10 段：
    # source_index = 0 ~ 9。
    source_index: Mapped[int] = mapped_column(
        Integer,
    )

    # Chunk 原始文本内容。
    content: Mapped[str] = mapped_column(
        Text,
    )

    # Chunk 对应的向量 Embedding。
    #
    # 当前以 JSON 字符串形式存储，例如：
    # [0.12, -0.35, 0.88, ...]
    #
    # 如果尚未生成向量，则允许为 None。
    embedding_json: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # Chunk 创建时间。
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=now,
    )
