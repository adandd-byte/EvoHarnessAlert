from __future__ import annotations

from dataclasses import dataclass, field, replace  # 数据类 + 不可变替换
from enum import Enum                              # 枚举
from typing import Any                             # 任意类型


class AgentEventType(str, Enum):
    TURN_STARTED = "TURN_STARTED"        # 一轮协作开始
    TASK_CREATED = "TASK_CREATED"        # 任务创建
    TASK_CLAIMED = "TASK_CLAIMED"        # 任务被认领
    TASK_RELEASED = "TASK_RELEASED"      # 任务被释放
    TASK_CLOSED = "TASK_CLOSED"          # 任务关闭
    MESSAGE_SENT = "MESSAGE_SENT"        # 消息发送
    ARTIFACT_PUBLISHED = "ARTIFACT_PUBLISHED"   # 普通产物发布
    CRITIQUE_PUBLISHED = "CRITIQUE_PUBLISHED"   # 批判产物发布
    REVISION_REQUESTED = "REVISION_REQUESTED"   # 请求修订
    SAFETY_OVERRIDE = "SAFETY_OVERRIDE"  # 安全越权/覆盖
    FINAL_ACCEPTED = "FINAL_ACCEPTED"    # 最终采纳
    ROUND_STARTED = "ROUND_STARTED"      # 新一轮开始
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"  # 轮数预算耗尽


class TaskStatus(str, Enum):
    OPEN = "OPEN"        # 待认领
    CLAIMED = "CLAIMED"  # 已被认领
    BLOCKED = "BLOCKED"  # 阻塞
    CLOSED = "CLOSED"    # 已关闭


class TaskPriority(str, Enum):
    LOW = "LOW"              # 低
    NORMAL = "NORMAL"        # 正常
    HIGH = "HIGH"            # 高
    CRITICAL = "CRITICAL"    # 紧急


PRIORITY_ORDER = {  # 优先级排序权重：数值越大越优先调度
    TaskPriority.LOW: 1,
    TaskPriority.NORMAL: 2,
    TaskPriority.HIGH: 3,
    TaskPriority.CRITICAL: 4,
}


@dataclass(frozen=True)
class AgentTask:
    id: str                            # 任务唯一 ID
    title: str                         # 任务标题
    description: str = ""              # 任务描述
    priority: TaskPriority = TaskPriority.NORMAL  # 优先级
    status: TaskStatus = TaskStatus.OPEN  # 当前状态
    required_capabilities: frozenset[str] = field(default_factory=frozenset)  # 所需能力
    created_by: str = "CoordinatorAgent"  # 创建者
    claimed_by: tuple[str, ...] = field(default_factory=tuple)  # 已认领者集合
    depends_on: tuple[str, ...] = field(default_factory=tuple)  # 依赖任务 ID
    metadata: dict[str, Any] = field(default_factory=dict)      # 扩展信息

    def claim(self, agent_name: str) -> "AgentTask":
        if agent_name in self.claimed_by:  # 已认领过则不重复
            return self
        return replace(self, status=TaskStatus.CLAIMED, claimed_by=(*self.claimed_by, agent_name))  # 标记认领

    def reopen(self) -> "AgentTask":
        return replace(self, status=TaskStatus.OPEN)  # 重新打开任务

    def close(self) -> "AgentTask":
        return replace(self, status=TaskStatus.CLOSED)  # 关闭任务


@dataclass(frozen=True)
class AgentClaim:
    agent: str        # 认领的 Agent 名
    task_id: str      # 认领的任务 ID
    confidence: float # 认领置信度
    reason: str       # 认领理由


@dataclass(frozen=True)
class AgentMessage:
    id: str                          # 消息 ID
    sender: str                      # 发送者
    recipient: str                   # 接收者
    content: str                     # 消息内容
    task_id: str = ""                # 关联任务
    kind: str = "REQUEST"            # 消息类型
    metadata: dict[str, Any] = field(default_factory=dict)  # 扩展信息


@dataclass(frozen=True)
class AgentArtifact:
    id: str                             # 产物 ID
    owner: str                          # 发布者
    kind: str                           # 产物类型（intent/risk/context/...）
    payload: dict[str, Any]             # 产物数据
    confidence: float = 1.0             # 产物的置信度
    task_id: str = ""                   # 关联任务
    metadata: dict[str, Any] = field(default_factory=dict)  # 扩展信息


@dataclass(frozen=True)
class AgentEvent:
    type: AgentEventType  # 事件类型
    actor: str            # 触发者
    task_id: str = ""     # 关联任务
    artifact_id: str = "" # 关联产物
    message: str = ""     # 事件描述
    metadata: dict[str, Any] = field(default_factory=dict)  # 扩展信息


@dataclass(frozen=True)
class AgentTurnResult:
    messages: tuple[AgentMessage, ...] = field(default_factory=tuple)  # 本轮发出的消息
    artifacts: tuple[AgentArtifact, ...] = field(default_factory=tuple)  # 本轮产出的产物
    tasks: tuple[AgentTask, ...] = field(default_factory=tuple)        # 本轮派生的新任务
    events: tuple[AgentEvent, ...] = field(default_factory=tuple)      # 本轮触发的事件
    close_task: bool = True  # 执行完后是否关闭任务


@dataclass(frozen=True)
class CollaborationBlackboard:
    turn_id: str                                 # 本轮唯一 ID
    user_id: int | None = None                   # 用户 ID
    session_id: str = ""                         # 会话 ID
    user_input: str = ""                         # 用户原始输入
    model_input: str = ""                        # 脱敏后送入模型的输入
    tasks: dict[str, AgentTask] = field(default_factory=dict)        # 任务表（id -> task）
    messages: tuple[AgentMessage, ...] = field(default_factory=tuple)  # 消息流
    artifacts: tuple[AgentArtifact, ...] = field(default_factory=tuple)  # 产物流
    events: tuple[AgentEvent, ...] = field(default_factory=tuple)    # 事件流
    final_artifact_id: str = ""                  # 最终采纳的产物 ID

    def add_task(self, task: AgentTask) -> "CollaborationBlackboard":
        tasks = dict(self.tasks)   # 拷贝任务表
        tasks[task.id] = task      # 加入新任务
        return replace(self, tasks=tasks)  # 返回新黑板

    def update_task(self, task: AgentTask) -> "CollaborationBlackboard":
        return self.add_task(task)  # 复用 add_task 覆盖更新

    def append_event(self, event: AgentEvent) -> "CollaborationBlackboard":
        return replace(self, events=(*self.events, event))  # 追加事件

    def send_message(self, message: AgentMessage) -> "CollaborationBlackboard":
        # 追加消息并登记一条 MESSAGE_SENT 事件
        return replace(self, messages=(*self.messages, message)).append_event(
            AgentEvent(
                type=AgentEventType.MESSAGE_SENT,  # 消息已发送事件
                actor=message.sender,
                task_id=message.task_id,
                message=message.content,
                metadata={"recipient": message.recipient, "kind": message.kind},
            )
        )

    def add_artifact(self, artifact: AgentArtifact) -> "CollaborationBlackboard":
        # 批判产物登记 CRITIQUE_PUBLISHED，其余登记 ARTIFACT_PUBLISHED
        event_type = AgentEventType.CRITIQUE_PUBLISHED if artifact.kind == "critique" else AgentEventType.ARTIFACT_PUBLISHED
        return replace(self, artifacts=(*self.artifacts, artifact)).append_event(
            AgentEvent(
                type=event_type,  # 按产物类型选事件类型
                actor=artifact.owner,
                task_id=artifact.task_id,
                artifact_id=artifact.id,
                message=artifact.kind,
                metadata={"confidence": artifact.confidence},
            )
        )

    def apply_turn_result(self, task: AgentTask, agent_name: str, result: AgentTurnResult) -> "CollaborationBlackboard":
        # 把一个 Agent 的执行结果落地到黑板：消息、产物、派生任务、任务状态
        board = self
        for message in result.messages:  # 落地消息
            board = board.send_message(message)
        for artifact in result.artifacts:  # 落地产物
            board = board.add_artifact(artifact)
        for follow_up in result.tasks:  # 落地派生任务
            if follow_up.id not in board.tasks:  # 不重复创建
                board = board.add_task(follow_up).append_event(
                    AgentEvent(
                        type=AgentEventType.TASK_CREATED,  # 新任务事件
                        actor=agent_name,
                        task_id=follow_up.id,
                        message=follow_up.title,
                    )
                )
        if result.close_task:  # 执行完则关闭任务
            board = board.update_task(task.close()).append_event(
                AgentEvent(type=AgentEventType.TASK_CLOSED, actor=agent_name, task_id=task.id, message=task.title)
            )
        else:  # 否则重新打开待继续
            board = board.update_task(task.reopen())
        for event in result.events:  # 落地额外事件
            board = board.append_event(event)
        return board

    def open_tasks(self) -> list[AgentTask]:
        return [task for task in self.tasks.values() if task.status == TaskStatus.OPEN]  # 待处理任务

    def artifacts_by_kind(self, kind: str) -> list[AgentArtifact]:
        return [artifact for artifact in self.artifacts if artifact.kind == kind]  # 按类型取产物

    def latest_artifact(self, kind: str, owner: str | None = None) -> AgentArtifact | None:
        for artifact in reversed(self.artifacts):  # 从最新往前找
            if artifact.kind == kind and (owner is None or artifact.owner == owner):  # 匹配类型与 owner
                return artifact
        return None

    def messages_for(self, agent_name: str) -> list[AgentMessage]:
        return [message for message in self.messages if message.recipient in {agent_name, "*"}]  # 给某 Agent 的消息

    def has_artifact(self, kind: str) -> bool:
        return self.latest_artifact(kind) is not None  # 是否存在某类产物

    def accepted_artifact(self) -> AgentArtifact | None:
        if not self.final_artifact_id:  # 无采纳则返回 None
            return None
        return next((artifact for artifact in self.artifacts if artifact.id == self.final_artifact_id), None)  # 找被采纳产物

    def accept_final(self, artifact_id: str, actor: str, reason: str) -> "CollaborationBlackboard":
        # 记录最终采纳的产物，并登记 FINAL_ACCEPTED 事件
        return replace(self, final_artifact_id=artifact_id).append_event(
            AgentEvent(
                type=AgentEventType.FINAL_ACCEPTED,  # 最终采纳事件
                actor=actor,
                artifact_id=artifact_id,
                message=reason,
            )
        )