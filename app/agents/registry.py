from __future__ import annotations

from dataclasses import dataclass, field  # dataclass 数据类
from enum import Enum                    # 枚举
from typing import Protocol              # 协议（接口）

from app.agents.events import AgentTask, AgentTurnResult, CollaborationBlackboard


class AgentCapability(str, Enum):
    UNDERSTANDING = "UNDERSTANDING"  # 理解/解析输入（规则实现）
    SAFETY = "SAFETY"                # 安全审查与止血把关
    CONTEXT = "CONTEXT"              # 准备上下文（记忆/RAG/Runbook）
    RESPONSE = "RESPONSE"            # 生成候选研判回复
    COORDINATION = "COORDINATION"    # 编排调度


@dataclass(frozen=True)
class AgentProfile:
    name: str                                    # Agent 名称
    capabilities: frozenset[AgentCapability] = field(default_factory=frozenset)  # 能力集合
    system_prompt: str = ""                      # 系统提示词
    memory_policy: str = "none"                  # 记忆策略标签
    model_profile: str = "default"               # 模型画像别名
    tool_permissions: frozenset[str] = field(default_factory=frozenset)          # 工具白名单


@dataclass(frozen=True)
class AgentDecision:
    claim: bool        # 是否认领任务
    confidence: float = 0.0  # 认领置信度（0~1）
    reason: str = ""   # 认领/不认领的理由


class AutonomousAgent(Protocol):
    # decide：表态是否认领（只读黑板，不改状态）
    def decide(self, task: AgentTask, board: CollaborationBlackboard) -> AgentDecision:
        ...

    # act：真正执行任务并返回结果（发布产物/消息/新任务）
    def act(self, task: AgentTask, board: CollaborationBlackboard) -> AgentTurnResult:
        ...


@dataclass(frozen=True)
class AgentCandidate:
    agent: AutonomousAgent  # 候选 Agent
    decision: AgentDecision # 对应决策


class AgentRegistry:
    # Agent 注册中心：持有全部 Agent，按任务返回候选名单
    def __init__(self, agents: list[AutonomousAgent]):
        self._agents = list(agents)  # 保存 Agent 列表

    @property
    def agents(self) -> list[AutonomousAgent]:
        return list(self._agents)  # 返回只读副本

    def candidates_for(self, task: AgentTask, board: CollaborationBlackboard) -> list[AutonomousAgent]:
        return [candidate.agent for candidate in self.candidate_decisions_for(task, board)]  # 取认领者

    def candidate_decisions_for(self, task: AgentTask, board: CollaborationBlackboard) -> list[AgentCandidate]:
        candidates = []  # 收集认领者
        for agent in self._agents:  # 遍历所有 Agent
            if not self._has_required_capability(agent, task):  # 能力不匹配则跳过
                continue
            decision = agent.decide(task, board)  # 让 Agent 表态
            if decision.claim:  # 认领才加入候选
                candidates.append(AgentCandidate(agent, decision))
        return sorted(candidates, key=lambda item: item.decision.confidence, reverse=True)  # 置信度降序

    def _has_required_capability(self, agent: AutonomousAgent, task: AgentTask) -> bool:
        if not task.required_capabilities:  # 无能力要求则任意 Agent 可做
            return True
        agent_capabilities = {capability.value for capability in agent.profile.capabilities}  # Agent 能力集
        return set(task.required_capabilities).issubset(agent_capabilities)  # 任务能力须是 Agent 子集