# 功能点与单元测试

最新增量：增加 `tests/test_replay_dataset.py` 的 100 条实际规则路由/样本契约检查和 1 条数据集整体检查后，全量结果为 **215 passed、3 skipped**。Playwright 另行验证了筛选、回放变体、备注、模拟接手、下载、接入、XSS、认证失败和响应式布局；不计入 pytest 数量。

2026-09-25 执行 `python3 -m pytest -q`：**114 passed、3 skipped、4 warnings**。本轮增加 92 个测试场景（含参数化组合），此前为 22 passed、3 skipped。测试数量不是代码覆盖率，也不是生产容量或模型效果指标。

## 功能对照

| 功能点 | 测试文件 | 验证内容 |
| --- | --- | --- |
| 优先级、类型、状态 | `tests/test_alert_rules.py` | P0-P3、英文别名、中文类型、未知输入兜底、全部 16 种优先级合并组合 |
| 去重指纹 | 同上 | 标签顺序和实例变化不影响指纹；服务、环境、类型不同不能误聚合 |
| Prometheus 接入转换 | 同上 | 缺少告警数组拒绝；恢复状态、时间、类型、优先级、指纹及原始输入保留 |
| 工具入队策略 | 同上 | P0/P1 台账+事件+通知；P2 台账+事件；P3 台账；没有事件不入通知任务 |
| 队列重试、死信、恢复 | `tests/test_queue_notifications.py` | 重试延迟、次数耗尽写死信、成功清理错误、重启恢复 RUNNING 任务 |
| 通知限流 | 同上 | 60 秒窗口边界、关闭限流、被限流不消耗尝试次数 |
| 通知投递 | 同上 | log、未知模式、SMTP 配置缺失、SMTP 异常、成功及重复通知幂等分支 |
| Skill 注册校验 | `tests/test_skill_validation.py` | front matter/name/workflow 缺失、目录名不一致、说明与安全边界缺失、空目录 |
| 最终采纳 | `tests/test_coordinator_gates.py` | 审查通过且绑定当前候选；置信度 0.6 边界；旧审查和拒绝审查不能放行 |
| 上下文路由与预算 | 同上 | P0-P2 上下文任务、P3 不触发；标签优先级覆盖文本猜测；预算耗尽不采纳未审查候选 |
| Redis 记忆 | `tests/test_memory.py` | 追加裁剪、TTL、替换、角色过滤、最近 N 条、读时脱敏、坏数据跳过、用户/会话隔离、不可用降级 |
| 历史摘要 | 同上 | 短历史保留、长历史压缩并保留最近八条、确定性摘要长度与脱敏 |
| RAG 评测公式 | `tests/test_rag_metrics.py` | 手算 Recall/Precision/RR/NDCG、未命中、重复文档召回率、空评测不能通过、非法数据和旧字段兼容 |
| 既有服务功能 | `tests/test_agent_services.py`、`tests/test_agent_runtime.py` | 模型配置、工具治理、轨迹、对话 Harness、候选审查与黑板更新 |
| MySQL 集成 | `tests/test_alerting.py` | 健康接口、Prometheus 落库与入队、相同指纹事件聚合；本次三项跳过 |

## 替身与验证边界

本轮新增单测不创建数据库，不启动 Redis，不调用模型、不发送邮件。数据库会话使用 Mock（可控替身），Redis 使用内存列表替身，SMTP 在发送入口拦截。它们验证业务分支和记录内容，不能证明 MySQL 事务、Redis 原子性或 SMTP 服务可用。

既有 `test_agent_services.py` 仍使用 SQLite fixture；这是遗留验证方式，不等于 MySQL 验收。本轮没有增加 SQLite 依赖。真实数据库测试只使用单独的 MySQL 测试库；测试会删除并重建表，不能连接业务库。

RAG 公式测试使用固定排名、独立手算期望值。当前 NDCG 的理想排序分母按已召回相关条目数计算；尚未改成完整金标集合的 IDCG。文档召回率按来源去重，Precision 按切片计数。它们是现有实现的计算口径，不能直接宣称满足生产金标评测标准。

尚未实现的 Redis 分布式 Claim、持久化检查点、端到端租户隔离、四类独立 Skill 和真实工具证据链，没有用替身“模拟通过”验收。当前测试也不证明默认协调器的占位安全审查具备完整防护能力。

## 本轮回归修复

1. `parse_dt` 先把带时区时间转换为 UTC，再保存无时区 UTC 时间，避免告警窗口偏移。
2. `load_recent` 使用 Redis 负索引读取最近 N 条，零或负数限制返回空。
3. 缓存中的合法 JSON 如果不是对象（如 `null`、数组），跳过处理，避免异常中断。

## 运行方式

```bash
python3 -m pytest -q
python3 -m pytest -q tests/test_queue_notifications.py
python3 -m pytest -q tests/test_memory.py tests/test_coordinator_gates.py
```

配置专用 MySQL 测试库后再执行全量测试。FastAPI `on_event` 弃用警告尚未处理，与本轮回归修复无关。
