"""EvoHarnessAlert 工程验收 Harness（engineering harness）。

参考 mindbridge-py 的 app/harness/runner.py 移植而来，用于在不依赖真实
MySQL / Redis / SMTP / Docker 的前提下，对本项目的核心链路做端到端验收：

1. 告警接入与路由 Harness   —— 归一化、Incident 聚合、任务入队、幂等、Prometheus 适配
2. Skills Harness           —— 技能库加载与安全边界校验
3. RAG Harness              —— 知识检索评估（mock knowledge，跑评测数据集算指标）
4. API Harness              —— FastAPI 接口层冒烟（健康检查、鉴权、告警接入、技能状态）
5. Tool Queue Harness       —— 任务执行成功、限流、重试与死信
6. Sandbox Harness          —— 轻量代码分析沙箱（process 后端：成功 / 超时 / 状态）
7. Estimation Harness       —— 单轮研判耗时估算与多服务并行分波

运行方式：
    python -m app.harness.runner                # 跑全部套件
    python -m app.harness.runner --suite api    # 只跑指定套件（可多次传）
    python -m app.harness.runner --json         # 只输出 JSON 报告

报告落盘：target/harness/harness-report.json；退出码 0=全部通过，1=存在失败。
"""

from __future__ import annotations

import argparse          # 命令行参数解析（--suite / --json）
import base64            # 生成 HTTP Basic 认证令牌
import json              # JSON 序列化（报告输出）
import os                # 读写环境变量（切换 sqlite / mock 模式）
import sys               # 程序退出码
import traceback         # 打印异常堆栈，方便定位失败原因
from dataclasses import dataclass, field          # 快速定义"只存数据"的类
from datetime import datetime                     # 报告时间戳
from pathlib import Path                          # 面向对象的路径操作
from typing import Any, Callable                  # 类型注解


# ---------------------------------------------------------------- 基础结构

class HarnessFailure(AssertionError):
    """单个检查项断言失败时抛出的异常；由 run_check 捕获并记录为 FAIL。"""


@dataclass
class CheckResult:
    """一个检查套件的执行结果。"""

    name: str                                          # 套件名称，如 "告警接入与路由 Harness"
    passed: bool                                       # 是否通过
    details: dict = field(default_factory=dict)        # 通过时的明细数据
    failures: list[str] = field(default_factory=list)  # 失败时的原因列表


@dataclass
class HarnessContext:
    """整套检查共享的运行上下文：项目根目录、输出目录、配置、数据库模块。"""

    root: Path          # 项目根目录
    target_dir: Path    # 输出目录（报告、临时 Excel 都放这里）
    settings: Any       # 全局配置对象（get_settings() 的结果）
    database: Any       # app.core.database 模块（engine / SessionLocal / Base）


def expect(condition: bool, message: str) -> None:
    """断言工具：条件不成立直接抛 HarnessFailure，终止当前套件。"""
    if not condition:
        raise HarnessFailure(message)


# ---------------------------------------------------------------- 入口

def main(argv: list[str] | None = None) -> int:
    # ===== 程序入口：解析参数 -> 配置环境 -> 逐个跑套件 -> 写报告 =====
    parser = argparse.ArgumentParser(description="EvoHarnessAlert 工程验收 Harness")
    parser.add_argument(
        "--suite",
        action="append",   # 允许重复传，比如 --suite routing --suite api
        choices=["routing", "skills", "rag", "api", "tool-queue", "sandbox", "estimation", "all"],
        default=None,
        help="要运行的检查套件，可多次指定；不传则运行全部",
    )
    parser.add_argument("--json", action="store_true", help="只输出 JSON 报告")
    args = parser.parse_args(argv)

    # 第 1 步：把环境切到"可测试"状态（sqlite 临时库、mock 模型、输出目录）
    configure_environment()
    # 第 2 步：构建上下文（重建数据库连接，让后续所有模块都用临时库）
    context = build_context()

    # 第 3 步：确定要跑哪些套件，逐个执行
    suites = resolve_suites(args.suite)
    results: list[CheckResult] = []
    for name, fn in suites:
        # 每个套件开始前都重置数据库，保证套件之间互不污染
        reset_database(context)
        results.append(run_check(name, fn, context))

    # 第 4 步：写报告并输出；退出码供 CI 判断
    report = write_report(context, results)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_report(report)
    return 0 if all(result.passed for result in results) else 1


def configure_environment() -> None:
    """把运行环境切换为可测试状态。

    - DATABASE_URL 指向 target/harness 下的临时 sqlite（每次运行前先删旧的）；
    - AI_PROVIDER=mock，不调用真实大模型；
    - TOOL_QUEUE_ENABLED=false，API 测试时不启动后台 worker 线程；
    - EXCEL_PATH 指向临时目录，避免污染 data/ 下的真实台账。
    环境变量优先级高于 .env 文件，因此无需改动本地 .env。
    """
    root = Path(__file__).resolve().parents[2]
    target_dir = root / "target" / "harness"
    target_dir.mkdir(parents=True, exist_ok=True)
    db_path = target_dir / "evoharness-alert-harness.sqlite3"
    for suffix in ["", "-wal", "-shm"]:
        candidate = Path(f"{db_path}{suffix}")
        if candidate.exists():
            candidate.unlink()  # 删除上一次运行残留的临时库

    os.environ["DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"
    os.environ["AI_PROVIDER"] = "mock"
    os.environ["AGENT_FRAMEWORK"] = "event_driven_multi_agent"
    os.environ["KNOWLEDGE_VECTOR_ENABLED"] = "false"
    os.environ["KNOWLEDGE_VECTOR_REQUIRED"] = "false"
    os.environ["TOOL_QUEUE_ENABLED"] = "false"
    os.environ["ALERT_EMAIL_DELIVERY_MODE"] = "log"
    os.environ["EXCEL_PATH"] = str((target_dir / "alert-harness-ledger.xlsx").as_posix())
    os.environ["RAG_EVAL_OUTPUT"] = str((target_dir / "rag-eval-report.json").as_posix())


def build_context() -> HarnessContext:
    """重建数据库连接并返回检查上下文。

    app.core.database 在模块导入时就根据环境变量创建了 engine，
    所以这里要显式替换 engine / SessionLocal，保证工具队列 worker
    这类"内部自建会话"的代码也落在临时 sqlite 上。
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.core.config import get_settings
    import app.core.database as database

    get_settings.cache_clear()  # 清掉旧配置缓存，让新环境变量生效
    settings = get_settings()
    if getattr(database, "engine", None) is not None:
        database.engine.dispose()  # 释放旧连接池
    database.engine = create_engine(settings.database_url, connect_args={"check_same_thread": False}, pool_pre_ping=True)
    database.SessionLocal = sessionmaker(bind=database.engine, autoflush=False, autocommit=False)
    return HarnessContext(
        root=Path(__file__).resolve().parents[2],
        target_dir=Path(__file__).resolve().parents[2] / "target" / "harness",
        settings=settings,
        database=database,
    )


def reset_database(context: HarnessContext) -> None:
    """把数据库恢复到初始状态：删表 -> 建表 -> 重新种子（用户、告警源、知识库）。"""
    from app.core.bootstrap import seed_data

    context.database.Base.metadata.drop_all(bind=context.database.engine)
    context.database.Base.metadata.create_all(bind=context.database.engine)
    db = context.database.SessionLocal()
    try:
        seed_data(db)
    finally:
        db.close()


def resolve_suites(requested: list[str] | None) -> list[tuple[str, Callable[[HarnessContext], dict]]]:
    """按命令行参数挑出要跑的套件；不传或传 all 则全量执行。"""
    all_suites: list[tuple[str, Callable[[HarnessContext], dict]]] = [
        ("告警接入与路由 Harness", run_alert_routing_harness),
        ("Skills Harness", run_skills_harness),
        ("RAG Harness", run_rag_harness),
        ("API Harness", run_api_harness),
        ("Tool Queue Harness", run_tool_queue_harness),
        ("Sandbox Harness", run_sandbox_harness),
        ("Estimation Harness", run_estimation_harness),
    ]
    if not requested or "all" in requested:
        return all_suites
    names = set(requested)
    aliases = {
        "routing": "告警接入与路由 Harness",
        "skills": "Skills Harness",
        "rag": "RAG Harness",
        "api": "API Harness",
        "tool-queue": "Tool Queue Harness",
        "sandbox": "Sandbox Harness",
        "estimation": "Estimation Harness",
    }
    picked = {aliases[item] for item in names}
    return [suite for suite in all_suites if suite[0] in picked]


def run_check(name: str, fn: Callable[[HarnessContext], dict], context: HarnessContext) -> CheckResult:
    """执行单个套件：正常返回 PASS；断言失败/异常都归一成 FAIL 并记录原因。"""
    try:
        return CheckResult(name=name, passed=True, details=fn(context))
    except HarnessFailure as exc:
        return CheckResult(name=name, passed=False, failures=[str(exc)])
    except Exception as exc:  # noqa: BLE001  任何未预期异常都不能让整个 harness 崩掉
        return CheckResult(
            name=name,
            passed=False,
            failures=[f"{type(exc).__name__}: {exc}", traceback.format_exc()],
        )


# ---------------------------------------------------------------- 套件 1：告警接入与路由

def run_alert_routing_harness(context: HarnessContext) -> dict:
    """验收告警接入主链路的路由规则与幂等性。

    覆盖：
    - severity 别名归一（critical -> P1、warning -> P2、info -> P3）；
    - alertType 别名归一（business -> BUSINESS、host -> HOST）；
    - P0/P1 建 Incident 并入队通知任务；P2 建 Incident 但不通知；P3 只落事件；
    - 相同 fingerprint 重复告警 -> 复用同一 Incident 且 alertCount 递增；
    - Prometheus payload -> 统一 Webhook 请求的适配。
    """
    from app.core.enums import AlertType, Severity, ToolJobKind
    from app.models.entities import Incident, ToolJob
    from app.schemas.dtos import AlertWebhookRequest
    from app.services.alerting import AlertIngestService

    db = context.database.SessionLocal()
    observed: dict[str, Any] = {}
    try:
        service = AlertIngestService(db, context.settings)

        # ---- 用例 1：P0 核心链路故障 -> 建 Incident + 3 个任务（含通知） ----
        p0 = service.ingest_webhook(AlertWebhookRequest(
            source="webhook",
            title="coupon-service 验券核心链路不可用",
            description="/coupon/verify 5xx 错误率超过 60%",
            labels={"service": "coupon-service", "city": "北京", "env": "prod"},
            annotations={"summary": "验券核心链路不可用", "手机号": "13800138000", "token": "sk-abcdef123456"},
            severity="P0",
            alertType="PROBLEM",
        ))
        expect(p0.alert.severity == Severity.P0.value, "P0 告警的严重级别被错误归一")
        expect(p0.alert.alert_type == AlertType.PROBLEM.value, "PROBLEM 类型被错误归一")
        expect(p0.incident is not None, "P0 告警没有创建 Incident")
        expect(ToolJobKind.NOTIFICATION_SEND.value in p0.queued_jobs, "P0 告警没有入队通知任务")
        expect(ToolJobKind.LEDGER_WRITE.value in p0.queued_jobs, "告警没有入队台账任务")
        p0_jobs = db.query(ToolJob).filter(ToolJob.alert_id == p0.alert.id).all()
        expect(len(p0_jobs) >= 3, f"P0 应至少入队 3 个任务，实际 {len(p0_jobs)}")

        # ---- 用例 2：severity 别名（critical -> P1）+ 类型别名（business -> BUSINESS） ----
        p1 = service.ingest_webhook(AlertWebhookRequest(
            source="webhook",
            title="支付成功率下降",
            labels={"service": "payment-service", "region": "华南"},
            severity="critical",
            alertType="business",
        ))
        expect(p1.alert.severity == Severity.P1.value, "critical 别名应归一为 P1")
        expect(p1.alert.alert_type == AlertType.BUSINESS.value, "business 别名应归一为 BUSINESS")
        expect(p1.incident is not None and ToolJobKind.NOTIFICATION_SEND.value in p1.queued_jobs, "P1 应建 Incident 并通知")

        # ---- 用例 3：P2 建 Incident 但不通知 ----
        p2 = service.ingest_webhook(AlertWebhookRequest(
            source="webhook",
            title="磁盘使用率超阈值",
            labels={"host": "node-cn-01"},
            severity="warning",
            alertType="host",
        ))
        expect(p2.alert.severity == Severity.P2.value, "warning 别名应归一为 P2")
        expect(p2.alert.alert_type == AlertType.HOST.value, "host 别名应归一为 HOST")
        expect(p2.incident is not None, "P2 应创建 Incident")
        expect(ToolJobKind.NOTIFICATION_SEND.value not in p2.queued_jobs, "P2 不应触发值班通知")

        # ---- 用例 4：P3 只落事件，不建 Incident ----
        p3 = service.ingest_webhook(AlertWebhookRequest(
            source="webhook",
            title="单实例偶发错误日志",
            severity="info",
            alertType="problem",
            status="firing",
        ))
        expect(p3.alert.severity == Severity.P3.value, "info 别名应归一为 P3")
        expect(p3.incident is None, "P3 不应创建 Incident")
        expect(ToolJobKind.INCIDENT_UPSERT.value not in p3.queued_jobs, "P3 不应入队 Incident 更新任务")

        # ---- 用例 5：相同 fingerprint 重复告警 -> Incident 复用 + 计数递增（幂等聚合） ----
        first = service.ingest_webhook(AlertWebhookRequest(
            source="webhook",
            title="订单接口延迟升高",
            labels={"service": "order-service"},
            severity="P2",
            alertType="PROBLEM",
        ))
        alert_count_before = first.incident.alert_count
        second = service.ingest_webhook(AlertWebhookRequest(
            source="webhook",
            title="订单接口延迟升高（重复上报）",
            fingerprint=first.alert.fingerprint,  # 指纹相同 -> 视为同一故障
            labels={"service": "order-service"},
            severity="P2",
            alertType="PROBLEM",
        ))
        expect(second.incident is not None and second.incident.id == first.incident.id, "相同指纹没有复用同一 Incident")
        expect(second.incident.alert_count == alert_count_before + 1, "重复告警的 Incident 计数没有递增")

        # ---- 用例 6：Prometheus payload 适配 ----
        prom = service.ingest_prometheus({
            "alerts": [{
                "status": "firing",
                "labels": {"alertname": "HighErrorRate", "service": "coupon-service", "severity": "critical"},
                "annotations": {"summary": "接口错误率过高", "description": "错误率超阈值"},
            }],
        })
        expect(len(prom) == 1, "Prometheus 单条告警应产出 1 个结果")
        expect(prom[0].alert.severity == Severity.P1.value, "Prometheus 的 critical 应归一为 P1")
        expect(prom[0].alert.title == "接口错误率过高", "Prometheus 的 summary 应转换为标题")

        # ---- 汇总观察值 ----
        observed["incidentCount"] = db.query(Incident).count()
        observed["queuedJobsBySeverity"] = {
            "P0": len(p0.queued_jobs),
            "P1": len(p1.queued_jobs),
            "P2": len(p2.queued_jobs),
            "P3": len(p3.queued_jobs),
        }
        observed["incidentReuse"] = {"firstCount": alert_count_before, "secondCount": second.incident.alert_count}
        return observed
    finally:
        db.close()


# ---------------------------------------------------------------- 套件 2：Skills

def run_skills_harness(context: HarnessContext) -> dict:
    """验收技能库：技能文件能加载、元数据完整、安全边界说明齐全。"""
    from app.services.skills import AlertSkillLibrary

    skills = AlertSkillLibrary.status_items()
    names = {item["name"] for item in skills}
    # 本项目的两个标准技能必须齐全
    for expected in ["alert_triage", "runbook_response"]:
        expect(expected in names, f"缺少标准技能：{expected}")
    # 每个技能都必须处于 READY，且路径指向 SKILL.md
    failed = [item for item in skills if item["status"] != "READY"]
    expect(not failed, f"存在加载异常的技能：{[(i['name'], i['issues']) for i in failed]}")
    expect(all(item["path"].endswith("/SKILL.md") for item in skills), "技能状态未暴露 SKILL.md 路径")
    # 每个技能都必须声明支持的告警类型和处置流程
    expect(all(len(item["alertTypes"]) > 0 for item in skills), "技能缺少 alert_types 声明")
    expect(all(len(item["workflow"]) > 0 for item in skills), "技能缺少 workflow 步骤")
    return {"skills": sorted(names), "count": len(skills)}


# ---------------------------------------------------------------- 套件 3：RAG

def run_rag_harness(context: HarnessContext) -> dict:
    """验收知识检索：用 mock knowledge 跑评测数据集，计算检索指标。

    说明：这里用 MockKnowledgeService（词面重叠排序）验证评测管线本身
    是通的、指标能算出来、报告能落盘；真实 MySQL 向量召回的评估由
    `python -m app.rag_eval.runner` 单独执行。
    """
    from app.rag_eval.runner import MockKnowledgeService, evaluate_case, normalize_dataset, seed_eval_knowledge
    from app.services.knowledge import KnowledgeService

    dataset_path = context.root / context.settings.rag_eval_dataset
    raw = json.loads(dataset_path.read_text(encoding="utf-8"))
    cases, dataset_top_k = normalize_dataset(raw)
    top_k = dataset_top_k or context.settings.knowledge_top_k
    expect(len(cases) >= 10, f"RAG 评测数据集太小：{len(cases)} 条")

    # mock 服务直接基于评测用例构造语料，保证"应当能召回"
    mock = MockKnowledgeService(cases)
    results = [evaluate_case(mock, case, top_k) for case in cases]
    total = max(1, len(results))
    metrics = {
        "totalCases": len(results),
        "topK": top_k,
        "hitRate": sum(1 for item in results if item["hit"]) / total,
        "mrr": sum(item["reciprocalRank"] for item in results) / total,
        "ndcgAtK": sum(item["ndcgAtK"] for item in results) / total,
    }
    expect(metrics["hitRate"] >= 0.8, f"mock 召回命中率过低：{metrics['hitRate']:.3f}")
    expect(metrics["mrr"] >= 0.5, f"mock MRR 过低：{metrics['mrr']:.3f}")

    # 顺便验证真实 KnowledgeService 的 ingest/status 在临时库上是通的
    db = context.database.SessionLocal()
    try:
        service = KnowledgeService(db, context.settings)
        seed_eval_knowledge(service, cases)
        status = service.status()
        expect(status.get("databaseChunks", 0) >= 1, "知识入库后没有产生任何 chunk")
    finally:
        db.close()

    # 报告落盘，便于人工查看
    output = context.target_dir / "rag-harness-report.json"
    output.write_text(json.dumps({"createdAt": datetime.utcnow().isoformat(), "metrics": metrics}, ensure_ascii=False, indent=2), encoding="utf-8")
    return metrics | {"report": str(output), "knowledgeStatus": status}


# ---------------------------------------------------------------- 套件 4：API

def basic_auth(username: str, password: str) -> dict[str, str]:
    """构造 HTTP Basic 认证头。"""
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def run_api_harness(context: HarnessContext) -> dict:
    """验收 FastAPI 接口层：健康检查、鉴权、告警接入、技能状态、知识管理。"""
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app()
    admin_auth = basic_auth("admin", "admin123")
    observed: dict[str, Any] = {}
    with TestClient(app) as client:  # with 语句触发 startup（建表 + 种子数据）
        # 健康检查
        health = client.get("/actuator/health")
        expect(health.status_code == 200 and health.json()["status"] == "UP", "健康检查失败")

        # 鉴权：未登录访问管理接口应被拒绝
        anonymous = client.get("/api/admin/alerts")
        expect(anonymous.status_code in {401, 403}, f"未登录访问管理接口应被拒绝，实际 {anonymous.status_code}")

        # 登录后的资料接口
        profile = client.get("/api/profile", headers=admin_auth)
        expect(profile.status_code == 200 and profile.json()["username"] == "admin", "管理员资料接口异常")

        # 告警接入：P0 webhook -> 返回 incidentId 和任务列表
        ingest = client.post("/api/alerts/webhook", json={
            "source": "webhook",
            "title": "验券核心链路不可用",
            "description": "5xx 错误率超 60%",
            "labels": {"service": "coupon-service", "env": "prod"},
            "severity": "P0",
            "alertType": "PROBLEM",
        })
        expect(ingest.status_code == 200, f"告警接入失败：{ingest.status_code} {ingest.text}")
        body = ingest.json()
        expect(body["incidentId"] is not None, "P0 告警接口未返回 incidentId")
        expect("NOTIFICATION_SEND" in body["queuedJobs"], "P0 告警接口未返回通知任务")

        # Agent 状态：技能列表暴露给前端
        agent_status = client.get("/api/agent/status", headers=admin_auth)
        expect(agent_status.status_code == 200, "Agent 状态接口失败")
        expect(len(agent_status.json()["skills"]) >= 2, "Agent 状态暴露的技能过少")

        # 知识管理：入库 + 状态
        knowledge = client.post("/api/admin/knowledge", headers=admin_auth, json={
            "source": "harness-note",
            "content": "验券失败时优先检查 coupon-service 与 merchant-service 的调用链，确认是否下游拖垮上游。",
        })
        expect(knowledge.status_code == 200 and knowledge.json()["chunks"] >= 1, "知识入库失败")
        status = client.get("/api/admin/knowledge/status", headers=admin_auth)
        expect(status.status_code == 200 and status.json()["databaseChunks"] >= 1, "知识状态接口异常")
        observed["knowledgeStatus"] = status.json()
    return observed


# ---------------------------------------------------------------- 套件 5：Tool Queue

def run_tool_queue_harness(context: HarnessContext) -> dict:
    """验收工具队列：任务成功执行、限流生效、失败转死信。

    不启动后台线程，直接调用 worker 的内部方法，保证结果可断言。
    """
    from app.core.enums import ToolJobKind, ToolJobStatus, ToolStatus
    from app.models.entities import DeadLetterRecord, ToolJob
    from app.services.alerting import AlertIngestService
    from app.schemas.dtos import AlertWebhookRequest
    from app.services.tool_queue import RateLimiter, ToolQueueWorker
    from app.services.tools import ToolOrchestrationService

    db = context.database.SessionLocal()
    worker = ToolQueueWorker(context.settings)
    observed: dict[str, Any] = {}
    try:
        # 准备一条 P0 告警（会自动入队 3 类任务）
        result = AlertIngestService(db, context.settings).ingest_webhook(AlertWebhookRequest(
            source="webhook",
            title="验券核心链路不可用",
            labels={"service": "coupon-service"},
            severity="P0",
            alertType="PROBLEM",
        ))
        alert_id = result.alert.id
        incident_id = result.incident.id
        jobs = {job.kind: job for job in db.query(ToolJob).filter(ToolJob.alert_id == alert_id).all()}
        expect(ToolJobKind.LEDGER_WRITE.value in jobs, "台账任务未入队")
        expect(ToolJobKind.NOTIFICATION_SEND.value in jobs, "通知任务未入队")

        # ---- 成功路径：手动执行台账任务 -> 状态 SUCCESS ----
        ledger_job = jobs[ToolJobKind.LEDGER_WRITE.value]
        ledger_job.status = ToolJobStatus.RUNNING.value
        db.add(ledger_job)
        db.commit()
        worker._run_job(ledger_job.id)
        db.refresh(ledger_job)
        expect(ledger_job.status == ToolJobStatus.SUCCESS.value, f"台账任务执行失败：{ledger_job.last_error}")

        # ---- 工具编排层幂等：同一告警重复写台账 -> 复用同一条记录 ----
        tools = ToolOrchestrationService(db, context.settings)
        alert = db.get(type(result.alert), alert_id)  # 重新取一份告警实体
        first_record = tools.write_ledger(alert)
        second_record = tools.write_ledger(alert)
        expect(first_record.status == ToolStatus.SUCCESS.value, "台账写入状态异常")
        expect(first_record.id == second_record.id, "台账写入不幂等，重复告警产生了两条记录")

        # ---- 限流：RateLimiter 每分钟 1 次 -> 第二次应被拒绝并返回等待时间 ----
        limiter = RateLimiter(1)
        first_allowed, _ = limiter.allow()
        second_allowed, retry_after = limiter.allow()
        expect(first_allowed, "限流器拒绝了第一次请求")
        expect(not second_allowed and retry_after > 0, "限流器没有拦截第二次请求")

        # ---- 死信：attempts 已达上限的任务失败后应转 DEAD 并落死信表 ----
        dead_job = ToolJob(
            alert_id=alert_id,
            incident_id=incident_id,
            kind=ToolJobKind.LEDGER_WRITE.value,
            status=ToolJobStatus.RUNNING.value,
            attempts=3,
            max_attempts=3,
        )
        db.add(dead_job)
        db.commit()
        worker._fail_or_dead_letter(db, dead_job.id, RuntimeError("harness 注入的失败"))
        db.refresh(dead_job)
        dead_letter = db.query(DeadLetterRecord).filter(DeadLetterRecord.job_id == dead_job.id).first()
        expect(dead_job.status == ToolJobStatus.DEAD.value, "达到重试上限的任务没有转 DEAD")
        expect(dead_letter is not None, "失败任务没有写入死信记录")

        observed["alertId"] = alert_id
        observed["ledgerJobId"] = ledger_job.id
        observed["ledgerIdempotent"] = first_record.id == second_record.id
        observed["deadLetterId"] = dead_letter.id
        return observed
    finally:
        worker.stop()
        db.close()


# ---------------------------------------------------------------- 套件 6：Sandbox

def run_sandbox_harness(context: HarnessContext) -> dict:
    """验收轻量代码分析沙箱（process 后端）：正常执行、超时保护、状态自检。"""
    from app.sandbox import create_sandbox

    sandbox = create_sandbox(context.settings)
    expect(sandbox.enabled, "沙箱未启用")

    # ---- 正常执行：echo 命令应成功返回 ----
    ok = sandbox.execute(["bash", "-c", "echo harness-ok"])
    expect(ok.outcome == "succeeded" and "harness-ok" in ok.stdout, f"沙箱正常执行失败：{ok.stderr}")
    expect(ok.duration_ms >= 0, "沙箱耗时统计异常")

    # ---- 超时保护：sleep 超过时限应被终止并标记 timeout ----
    slow = sandbox.execute(["bash", "-c", "sleep 5"], timeout=1.0)
    expect(slow.outcome == "timeout", f"超时任务未被拦截，结果为 {slow.outcome}")

    # ---- 仓库克隆与目录内执行（clone 本地目录模拟，不依赖外网） ----
    mini_repo = context.target_dir / "harness-mini-repo"
    mini_repo.mkdir(parents=True, exist_ok=True)
    (mini_repo / "app.py").write_text("def verify_coupon():\n    raise NullPointerException\n", encoding="utf-8")
    grep = sandbox.execute(
        ["bash", "-c", "grep -rn 'NullPointerException' --include=*.py . | head -n 5"],
        cwd=mini_repo,
    )
    expect(grep.outcome == "succeeded" and "NullPointerException" in grep.stdout, "沙箱目录内检索代码失败")

    # ---- 状态自检：backend 状态字段齐全 ----
    status = sandbox.backend_status()
    for key in ["backend", "workspace", "timeoutSeconds", "memoryMb"]:
        expect(key in status, f"沙箱状态缺少字段：{key}")
    return {"backend": status["backend"], "echoMs": ok.duration_ms, "timeoutMarked": slow.outcome, "grepHit": "NullPointerException" in grep.stdout}


# ---------------------------------------------------------------- 套件 7：Estimation

def run_estimation_harness(context: HarnessContext) -> dict:
    """验收耗时估算：串行/并行结构正确、并行分波不超过上限、总额一致。"""
    from app.core.enums import AlertType, Severity
    from app.services.estimation import estimate_alert_assessment_time, format_estimate, plan_parallel_analysis

    # ---- 并行分波：8 个服务、并行上限 5 -> 应分成 2 波，且每波不超过 5 ----
    waves = plan_parallel_analysis(["s1", "s2", "s3", "s4", "s5", "s6", "s7", "s8"], max_parallel=5)
    expect(len(waves) == 2, f"8 个服务并行上限 5 应分 2 波，实际 {len(waves)} 波")
    expect(all(len(wave) <= 5 for wave in waves), "并行波次超过上限")
    expect(sum(len(wave) for wave in waves) == 8, "分波后服务丢失")

    # ---- P1/PROBLEM 波及 5 个服务的完整估算 ----
    estimate = estimate_alert_assessment_time(
        Severity.P1, AlertType.PROBLEM,
        ["coupon-service", "payment-service", "order-service", "deal-service", "merchant-service"],
        settings=context.settings,
    )
    serial_sum = sum(stage.total_ms() for stage in estimate.stages if not stage.parallel)
    parallel_sum = sum(stage.total_ms() for stage in estimate.stages if stage.parallel)
    expect(estimate.serial_ms == serial_sum, "串行阶段汇总与明细不一致")
    expect(estimate.parallel_ms == parallel_sum, "并行阶段汇总与明细不一致")
    expect(estimate.total_ms == estimate.serial_ms + estimate.parallel_ms, "总耗时不等于串行 + 并行")
    # 5 个服务在并行上限 5 内应只占 1 波，并行耗时 = 单个沙箱耗时（不是 5 倍）
    sandbox_stages = [stage for stage in estimate.stages if stage.parallel]
    expect(len(sandbox_stages) == 1, "5 个服务应只产生 1 个并行波次")
    expect(sandbox_stages[0].total_ms() == context.settings.estimation_default_tool_ms, "并行沙箱耗时未按单波计算")

    text = format_estimate(estimate)
    expect("单轮研判预估" in text, "耗时估算文案缺失")
    return {
        "severity": estimate.severity.value,
        "alertType": estimate.alert_type.value,
        "serialMs": estimate.serial_ms,
        "parallelMs": estimate.parallel_ms,
        "totalMs": estimate.total_ms,
        "waves": len(waves),
    }


# ---------------------------------------------------------------- 报告输出

def write_report(context: HarnessContext, results: list[CheckResult]) -> dict:
    """汇总所有套件结果，写入 JSON 报告文件并返回。"""
    report = {
        "createdAt": datetime.utcnow().isoformat(),
        "environment": {
            "databaseUrl": context.settings.database_url,
            "aiProvider": context.settings.ai_provider,
            "agentFramework": context.settings.agent_framework,
            "toolQueueEnabled": context.settings.tool_queue_enabled,
            "excelPath": context.settings.excel_path,
        },
        "passed": all(result.passed for result in results),
        "results": [
            {
                "name": result.name,
                "passed": result.passed,
                "details": result.details,
                "failures": result.failures,
            }
            for result in results
        ],
    }
    output = context.target_dir / "harness-report.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    report["reportPath"] = str(output)
    return report


def print_report(report: dict) -> None:
    """人话版控制台输出：每套件一行 PASS/FAIL，失败原因缩进展示。"""
    print("EvoHarnessAlert 工程验收 Harness")
    print(f"报告: {report['reportPath']}")
    print("")
    for result in report["results"]:
        status = "PASS" if result["passed"] else "FAIL"
        print(f"[{status}] {result['name']}")
        if result["passed"] and result["details"]:
            compact = json.dumps(result["details"], ensure_ascii=False, default=str)
            print(f"       {compact[:900]}")
        for failure in result["failures"]:
            print(f"       {failure}")
    print("")
    print("总体: PASS" if report["passed"] else "总体: FAIL")


if __name__ == "__main__":
    sys.exit(main())