from __future__ import annotations

import json  # JSON 编解码
import logging  # 日志
import threading  # 线程
import time  # 时间
from collections import deque  # 双端队列
from concurrent.futures import ThreadPoolExecutor  # 线程池
from datetime import datetime, timedelta  # 时间

from app.core.config import Settings  # 全局配置
from app.core.database import SessionLocal  # 数据库会话工厂
from app.core.enums import ToolJobKind, ToolJobStatus, ToolStatus  # 工具状态枚举
from app.models.entities import AlertEvent, DeadLetterRecord, Incident, ToolJob  # 实体
from app.services.tools import ToolOrchestrationService  # 工具编排

logger = logging.getLogger(__name__)  # 模块日志


class RateLimiter:
    # 滑动窗口限流器：按分钟限制事件数
    def __init__(self, limit_per_minute: int):
        self.limit = max(0, limit_per_minute)  # 每分钟上限
        self.events: deque[float] = deque()  # 事件时间戳队列
        self.lock = threading.Lock()  # 并发锁

    def allow(self) -> tuple[bool, float]:
        # 判断是否放行，返回 (是否放行, 需等待秒数)
        if self.limit <= 0:  # 无限流
            return True, 0.0
        now_ts = time.monotonic()  # 当前单调时钟
        with self.lock:  # 加锁
            while self.events and now_ts - self.events[0] >= 60.0:  # 移除超过一分钟的历史事件
                self.events.popleft()
            if len(self.events) < self.limit:  # 未达上限放行
                self.events.append(now_ts)
                return True, 0.0
            return False, max(1.0, 60.0 - (now_ts - self.events[0]))  # 限流并返回等待时间


class ToolQueueWorker:
    # 工具队列工作者：轮询待处理任务并分发到对应线程池
    def __init__(self, settings: Settings):
        self.settings = settings  # 全局配置
        self.stop_event = threading.Event()  # 停止信号
        self.dispatcher: threading.Thread | None = None  # 调度线程
        self.excel_executor = ThreadPoolExecutor(max_workers=max(1, settings.tool_queue_excel_workers), thread_name_prefix="evo-ledger")  # 台账线程池
        self.email_executor = ThreadPoolExecutor(max_workers=max(1, settings.tool_queue_email_workers), thread_name_prefix="evo-notify")  # 通知线程池
        self.email_limiter = RateLimiter(settings.alert_email_rate_limit_per_minute)  # 邮件限流

    def start(self) -> None:
        # 启动调度线程
        if not self.settings.tool_queue_enabled or self.dispatcher is not None:  # 未启用或已启动则跳过
            return
        self._recover_running_jobs()  # 恢复上次未完成任务
        self.dispatcher = threading.Thread(target=self._loop, name="evo-tool-dispatcher", daemon=True)  # 建调度线程
        self.dispatcher.start()  # 启动

    def stop(self) -> None:
        # 停止调度与线程池
        self.stop_event.set()  # 置停止信号
        if self.dispatcher is not None:  # 等待调度线程退出
            self.dispatcher.join(timeout=5)
        self.excel_executor.shutdown(wait=False, cancel_futures=True)  # 关闭台账池
        self.email_executor.shutdown(wait=False, cancel_futures=True)  # 关闭通知池

    def _loop(self) -> None:
        # 调度主循环
        while not self.stop_event.is_set():  # 未停止则循环
            try:
                self._dispatch_once()  # 调度一轮
            except Exception:  # 异常记录但继续
                logger.exception("工具队列调度失败")
            self.stop_event.wait(self.settings.tool_queue_poll_interval_seconds)  # 间隔轮询

    def _dispatch_once(self) -> None:
        # 取一批待执行任务并分发到线程池
        db = SessionLocal()  # 建会话
        try:
            jobs = db.query(ToolJob).filter(ToolJob.status == ToolJobStatus.PENDING.value, ToolJob.run_after <= datetime.utcnow()).order_by(ToolJob.created_at.asc()).limit(self.settings.tool_queue_batch_size).all()  # 查待执行
            for job in jobs:  # 逐个标记运行
                job.status = ToolJobStatus.RUNNING.value
                job.updated_at = datetime.utcnow()
                db.add(job)
                db.commit()
                self._executor_for(job).submit(self._run_job, job.id)  # 提交到对应线程池
        finally:
            db.close()  # 关闭会话

    def _executor_for(self, job: ToolJob) -> ThreadPoolExecutor:
        # 按任务类型选择线程池：通知走邮箱池，其余走台账池
        return self.email_executor if job.kind == ToolJobKind.NOTIFICATION_SEND.value else self.excel_executor

    def _run_job(self, job_id: int) -> None:
        # 执行单个任务的主入口
        db = SessionLocal()  # 建会话
        try:
            job = db.get(ToolJob, job_id)  # 取任务
            if job is None or job.status != ToolJobStatus.RUNNING.value:  # 无效则跳过
                return
            if job.kind == ToolJobKind.NOTIFICATION_SEND.value:  # 通知类先过限流
                allowed, retry_after = self.email_limiter.allow()  # 判断是否放行
                if not allowed:  # 被限流则重排队
                    self._requeue(db, job, "通知限流中，稍后重试", retry_after)
                    return
            job.attempts += 1  # 尝试次数加一
            job.updated_at = datetime.utcnow()
            db.add(job)
            db.commit()
            self._execute(db, job)  # 真正执行
            job.status = ToolJobStatus.SUCCESS.value  # 标记成功
            job.last_error = ""  # 清空错误
            job.updated_at = datetime.utcnow()
            db.add(job)
            db.commit()
        except Exception as exc:  # 失败则失败或投递死信
            self._fail_or_dead_letter(db, job_id, exc)
        finally:
            db.close()  # 关闭会话

    def _execute(self, db, job: ToolJob) -> None:
        # 按任务类型调用对应工具
        alert = db.get(AlertEvent, job.alert_id)  # 取告警
        if alert is None:  # 告警缺失
            raise RuntimeError(f"告警不存在：{job.alert_id}")
        incident = db.get(Incident, job.incident_id) if job.incident_id else None  # 取事件
        tools = ToolOrchestrationService(db, self.settings)  # 建编排服务
        if job.kind == ToolJobKind.LEDGER_WRITE.value:  # 台账写入
            record = tools.write_ledger(alert)  # 写台账
            if record.status != ToolStatus.SUCCESS.value:  # 失败则抛
                raise RuntimeError(record.message)
            return
        if job.kind == ToolJobKind.INCIDENT_UPSERT.value:  # 事件更新
            tools.record_incident_upsert(alert, incident)  # 登记事件
            return
        if job.kind == ToolJobKind.NOTIFICATION_SEND.value:  # 通知发送
            if incident is None:  # 事件必填
                raise RuntimeError("发送通知前事件不存在")
            record = tools.send_notification(alert, incident)  # 发通知
            if record.status != ToolStatus.SUCCESS.value:  # 失败则抛
                raise RuntimeError(record.message)
            return
        raise RuntimeError(f"未知任务类型：{job.kind}")

    def _requeue(self, db, job: ToolJob, reason: str, delay_seconds: float) -> None:
        # 任务重新排队，延迟再试
        job.status = ToolJobStatus.PENDING.value  # 回退待执行
        job.last_error = reason  # 记录原因
        job.run_after = datetime.utcnow() + timedelta(seconds=max(1.0, delay_seconds))  # 延迟
        job.updated_at = datetime.utcnow()
        db.add(job)
        db.commit()

    def _fail_or_dead_letter(self, db, job_id: int, exc: Exception) -> None:
        # 失败处理：重试次数耗尽则进死信，否则延迟重试
        job = db.get(ToolJob, job_id)  # 取任务
        if job is None:  # 不存在则跳过
            return
        message = f"{type(exc).__name__}: {exc}"  # 错误信息
        job.last_error = message  # 记录错误
        job.updated_at = datetime.utcnow()
        if job.attempts >= job.max_attempts:  # 达到最大重试
            job.status = ToolJobStatus.DEAD.value  # 置死信
            db.add(DeadLetterRecord(job_id=job.id, alert_id=job.alert_id, incident_id=job.incident_id, kind=job.kind, reason=message, payload=json.dumps({"alertId": job.alert_id, "kind": job.kind}, ensure_ascii=False)))  # 写死信记录
        else:  # 未达上限则重试
            job.status = ToolJobStatus.PENDING.value  # 回退待执行
            job.run_after = datetime.utcnow() + timedelta(seconds=self.settings.tool_queue_retry_delay_seconds * max(1, job.attempts))  # 指数延迟
        db.add(job)
        db.commit()

    def _recover_running_jobs(self) -> None:
        # 启动时把遗留的 RUNNING 任务还原为 PENDING，避免丢失
        db = SessionLocal()  # 建会话
        try:
            for job in db.query(ToolJob).filter(ToolJob.status == ToolJobStatus.RUNNING.value).all():  # 遍历运行中任务
                job.status = ToolJobStatus.PENDING.value  # 还原待执行
                job.last_error = "服务重启后恢复未完成任务"  # 记录原因
                job.run_after = datetime.utcnow()  # 立即可执行
                job.updated_at = datetime.utcnow()
                db.add(job)
            db.commit()
        finally:
            db.close()  # 关闭会话


_worker: ToolQueueWorker | None = None  # 全局单例


def get_tool_queue_worker(settings: Settings) -> ToolQueueWorker:
    # 获取全局唯一的队列工作者（懒加载单例）
    global _worker  # 引用全局
    if _worker is None:  # 未创建则初始化
        _worker = ToolQueueWorker(settings)
    return _worker