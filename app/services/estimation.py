from __future__ import annotations

"""单轮告警研判的耗时估算 与 多微服务并行分析规划。

用途：
1. 面试可量化：回答"分析一轮告警大约要多久、瓶颈在哪、如何压下来"。
2. 生产可落地：在告警入口记录预估耗时，用于 SLO、容量规划和观测（LLM/RAG/沙箱
   的分阶段耗时来自配置常量，可被真实遥测覆盖，观测值回写后再回归校准）。

模型：单条告警的端到端耗时 = 串行阶段相加 + 并行阶段取 max。
关键的可并行点：一条告警可能途经多个微服务，每个服务都要做"clone+静态分析"，
这一步天然可并行（每个服务一个沙箱/线程池），从而把 N×T_tool 压成 ceil(N/parallel)×T_tool。
"""

from dataclasses import dataclass, field
from typing import Iterable, Sequence

from app.core.config import Settings, get_settings
from app.core.enums import AlertType, Severity


@dataclass(frozen=True)
class StageEstimate:
    name: str
    parallel: bool  # True 表示可与其他 parallel 阶段取 max
    base_ms: int
    count: int = 1
    note: str = ""

    def total_ms(self) -> int:
        # 并行阶段：同一波内所有服务同时分析，耗时取单个（最慢）任务的耗时，
        # 而不是按服务数量相乘——这正是并行带来的收益。
        if self.parallel:
            return self.base_ms
        return self.base_ms * max(self.count, 1)


@dataclass(frozen=True)
class AlertTimeEstimate:
    severity: Severity
    alert_type: AlertType
    serial_ms: int
    parallel_ms: int
    stages: Sequence[StageEstimate] = field(default_factory=tuple)
    total_ms: int = 0

    def breakdown_serial_ms(self) -> int:
        return sum(s.total_ms() for s in self.stages if not s.parallel)


# 不同类型的处置 agent 步数，用于估 LLM 调用次数（对齐 handling_steps / runtime max_steps）
_STEPS_BY_TYPE = {
    AlertType.PROBLEM: 5,
    AlertType.BUSINESS: 4,
    AlertType.EVENT: 4,
    AlertType.HOST: 4,
}
_STEPS_BY_SEVERITY_PENALTY = {Severity.P0: 2, Severity.P1: 1, Severity.P2: 0, Severity.P3: 0}


def _llm_calls(severity: Severity, alert_type: AlertType) -> int:
    base = _STEPS_BY_TYPE.get(alert_type, 4)
    base += _STEPS_BY_SEVERITY_PENALTY.get(severity, 0)
    return base


def plan_parallel_analysis(microservices: Iterable[str], max_parallel: int | None = None, settings: Settings | None = None) -> list[list[str]]:
    """把待分析的多个微服务分成若干'波次'(wave)，同波内服务并行分析。

    返回 [[svc1, svc2,...], [svc3,...]]，每波不超过 max_parallel 个。
    """
    services = [str(s) for s in microservices if str(s).strip()]
    max_parallel = max_parallel or (settings or get_settings()).estimation_max_parallel
    parallel = max(1, max_parallel)
    return [services[i : i + parallel] for i in range(0, len(services), parallel)]


def estimate_alert_assessment_time(
    severity: Severity | str,
    alert_type: AlertType | str,
    microservices: Iterable[str] | None = None,
    settings: Settings | None = None,
) -> AlertTimeEstimate:
    settings = settings or get_settings()
    sev = severity if isinstance(severity, Severity) else Severity(str(severity).upper())
    atype = alert_type if isinstance(alert_type, AlertType) else AlertType(str(alert_type).upper())
    microservices = list(microservices or [])

    rag = settings.estimation_default_rag_ms
    llm = settings.estimation_default_llm_ms
    tool = settings.estimation_default_tool_ms
    parallel = settings.estimation_max_parallel

    # 串行阶段：入库归一、Incident 聚合、RAG 检索、基础 LLM 研判、评审采纳、落账
    serial_stages = [
        StageEstimate("ingest_normalize", False, 10),
        StageEstimate("incident_upsert", False, 10),
        StageEstimate("rag_retrieval", False, rag, 1),
        StageEstimate("llm_triage", False, llm, _llm_calls(sev, atype)),
        StageEstimate("final_accept", False, 100),
        StageEstimate("ledger_notify", False, 200),
    ]
    serial_ms = sum(s.total_ms() for s in serial_stages)

    # 并行阶段：跨微服务的沙箱代码分析，按波次取 max
    parallel_stages: list[StageEstimate] = []
    waves = plan_parallel_analysis(microservices, parallel, settings)
    for idx, wave in enumerate(waves):
        parallel_stages.append(StageEstimate(f"sandbox_analysis_wave{idx + 1}", True, tool, len(wave), note=",".join(wave) if wave else ""))
    parallel_ms = sum(s.total_ms() for s in parallel_stages) if parallel_stages else 0

    total = serial_ms + parallel_ms
    return AlertTimeEstimate(
        severity=sev,
        alert_type=atype,
        serial_ms=serial_ms,
        parallel_ms=parallel_ms,
        stages=[*serial_stages, *parallel_stages],
        total_ms=total,
    )


def format_estimate(estimate: AlertTimeEstimate) -> str:
    lines = [f"{estimate.severity.value}/{estimate.alert_type.value} 单轮研判预估：{estimate.total_ms}ms"]
    for stage in estimate.stages:
        kind = "并行" if stage.parallel else "串行"
        note = f" x{stage.count}" if stage.count > 1 else ""
        lines.append(f"  - [{kind}] {stage.name}{note}: {stage.total_ms()}ms{(' (' + stage.note + ')') if stage.note else ''}")
    lines.append(f"  串行合计 {estimate.serial_ms}ms + 并行合计 {estimate.parallel_ms}ms = {estimate.total_ms}ms")
    return "\n".join(lines)