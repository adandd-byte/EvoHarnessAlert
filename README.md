# EvoHarnessAlert 运维告警 Agent 平台

简历参考：[项目背景、技术栈与 50 条可组合亮点](docs/resume-bullets.md)，含后端、Agent、RAG、全栈和测试岗位的 4～5 条组合示例。界面与样本：[中文工作台及回放数据](docs/frontend-dataset.md)。

> 源码交付入口：先阅读 [交付说明](DELIVERY.md) 和 [飞书规格与代码对照](docs/spec-code-map.md)。当前为开发快照；本文中的设计方向、生产扩展和容量估算不等于已实现或已验收能力。打包命令：`bash scripts/package-release.sh`。

EvoHarnessAlert 是一个面向生产环境的多 Agent 告警研判与处置平台。它不是重新发明告警规则，也不是替代 SRE 做最终决策，而是在 P0/P1/P2/P3 告警已经被监控系统触发之后，帮助值班人员把告警文本、服务标签、历史事件、Runbook、日志线索、代码线索和指标大盘组织成一份可追溯的证据链报告。

当前代码已经实现：通用 Webhook 接入、Prometheus Alertmanager 接入、P0/P1/P2/P3 优先级归一、PROBLEM/BUSINESS/EVENT/HOST 类型识别、Incident 聚合、Agent Trace、MySQL 持久化、Redis 配置、异步工具队列、Excel 兼容台账、log/SMTP 通知、RAG 知识库基础能力、MCP 工具服务、Skill 状态校验和中文后台控制台。群机器人通知、日志查询 Agent、代码分析 Agent、自动修复 PR、Prompt/Skill 自进化属于生产扩展方向，README 会明确标注。

## 0. 术语速查表

这份文档会保留一些英文工程术语，因为代码、接口字段和面试表达里经常会用到；同时给出中文解释，方便第一次读项目的人理解。

| 英文术语              | 中文解释              | 在本项目中的含义                                                     |
| --------------------- | --------------------- | -------------------------------------------------------------------- |
| Alert                 | 告警                  | 监控系统触发的一条异常信号，例如错误率过高、磁盘水位过高。           |
| Severity / Priority   | 告警优先级            | P0/P1/P2/P3，P0 最紧急，P3 最低。                                    |
| P0                    | 最高优先级            | 核心链路中断、全站不可用、重大资损等，需要立即应急响应。             |
| P1                    | 高优先级              | 核心服务或重要业务明显受影响，需要立即通知值班人员。                 |
| P2                    | 中优先级              | 需要进入事件跟踪，但通常不需要全员应急。                             |
| P3                    | 低优先级              | 信息类或低影响告警，主要用于台账和趋势观察。                         |
| PROBLEM               | 问题/故障类告警       | 接口报错、服务不可用、依赖超时、空指针异常等。                       |
| BUSINESS              | 业务指标类告警        | 订单量、支付成功率、券核销率、转化率等业务指标异常。                 |
| EVENT                 | 事件/变更类告警       | 发布、配置变更、扩缩容、定时任务、依赖切换。                         |
| HOST                  | 主机/基础设施类告警   | CPU、内存、磁盘、网络、容器重启、节点异常。                          |
| Incident              | 故障事件 / 处置事件   | 多条相关 Alert 聚合后的事件，值班人员真正跟进的是 Incident。         |
| Fingerprint           | 告警指纹              | 用于判断两条告警是否相同或相关的稳定标识。                           |
| Runbook               | 排障手册              | 针对某类故障的标准排查步骤和止血动作。                               |
| SOP                   | 标准作业流程          | Standard Operating Procedure，团队约定好的操作规范。                 |
| RAG                   | 检索增强生成          | 先从知识库检索相关资料，再让大模型基于资料生成回答。                 |
| Embedding             | 向量化                | 把文本转换成向量，便于计算语义相似度。                               |
| Chunk                 | 文档切片              | 把长文档按标题、段落、接口等切成较小片段。                           |
| BM25                  | 关键词检索算法        | 适合匹配接口名、错误码、类名、方法名等精确词。                       |
| Rerank                | 重排                  | 对召回的候选文档重新排序，把最相关的放前面。                         |
| HNSW                  | 向量索引算法          | 常见近似最近邻检索结构，适合大规模向量搜索。                         |
| MCP                   | 模型工具协议          | Model Context Protocol，让 Agent 标准化调用外部工具。                |
| Skill                 | 技能文件              | 可检查、可测试、可维护的 Prompt/流程资产。                           |
| ToolJob               | 工具任务              | 需要异步执行的动作，例如写台账、发通知。                             |
| LedgerRecord          | 台账记录              | Ledger 是台账/账本的意思，这里记录告警处理留痕。                     |
| NotificationRecord    | 通知记录              | 邮件、SMTP、IM 群机器人、电话等通知动作的执行结果。                  |
| IM Bot / ChatOps      | 群机器人 / 聊天运维   | 值班群里 @机器人 后，机器人把告警转给后端研判，再把报告回传群聊。    |
| TraceId               | 请求链路 ID           | 一次请求穿过多个微服务时的关联标识，用来串起日志、调用链和代码路径。 |
| DeadLetter            | 死信记录              | 工具任务多次失败后进入死信，方便人工排查。                           |
| Trace                 | 轨迹 / 审计链         | 记录 Agent 每一步动作、证据和结论。                                  |
| Harness               | 编排外壳 / 工程检查器 | 线上 Harness 管业务编排；工程 Harness 管测试验收。                   |
| Runtime               | 运行时                | 多 Agent 执行、调度、共享黑板和事件流转的核心。                      |
| Blackboard            | 黑板                  | 多 Agent 共享的结构化状态，包含任务、证据、结论。                    |
| Claim-based Scheduler | 认领式调度器          | 让合适的 Agent 根据能力认领任务，而不是固定流水线。                  |

## 1. 项目简介与简历写法

一句话项目定位：EvoHarnessAlert 是一个基于事件驱动黑板式多 Agent 的告警研判平台，用于把生产告警转化为带证据链的值班报告，并在 P0/P1 场景下触发通知和后续处置流程。

简历可以这样写：

- 设计并实现运维告警 Agent 平台，支持 Webhook 和 Prometheus Alertmanager 接入，将外部告警统一归一为 P0/P1/P2/P3 优先级和 PROBLEM/BUSINESS/EVENT/HOST 类型。
- 基于黑板式多 Agent 协作模式，将告警定级、事件聚合、上下文检索、Runbook 匹配、证据链生成和处置建议拆分为可审计的 Agent 步骤，并写入 AgentRunTrace。
- 使用 MySQL 保存 AlertEvent、Incident、ToolJob、NotificationRecord、LedgerRecord 等核心业务数据，使用 Redis 承载短期上下文和热状态扩展能力。
- 构建 RAG 知识库链路，支持 Markdown Runbook 切分入库、BM25 检索、可选 Chroma 向量检索和本地 rerank 降级策略。
- 实现异步工具队列，支持告警台账写入、事件同步、P0/P1 通知发送、失败重试、限流和死信记录。
- 预留 MCP 工具接口，可扩展对接日志平台、监控平台、发布平台、工单系统和自动化运维工具。

面试介绍可以这样说：

> 生产环境里的告警规则通常已经由运维或 SRE 提前配置好了。这个项目解决的不是“要不要告警”，而是告警触发后如何快速理解它。比如 P0/P1 告警发生时，值班人员需要知道影响范围、相关服务、可能根因、已有证据、推荐 Runbook 和是否需要升级。EvoHarnessAlert 用多 Agent 黑板机制把告警、日志、代码、指标、知识库和工具调用组织起来，最终生成一份强引用证据链报告，帮助人更快做判断。

## 2. 前置知识

### 2.1 告警与风险

告警一般来自监控系统、日志系统、业务指标平台、主机监控或发布变更平台。运维人员会提前配置触发条件，例如错误率超过阈值、接口延迟升高、数据库连接池耗尽、核心业务指标断崖、机器磁盘水位过高等。

本项目使用 P0/P1/P2/P3 表达优先级：

- P0：最高级别，通常表示全站不可用、核心交易链路中断、重大资损、严重数据错误，需要立即拉起应急响应。
- P1：高优先级，核心服务或重要业务明显受影响，需要通知值班人员立即处理。
- P2：中优先级，需要进入事件跟踪并在值班窗口内处理。
- P3：低优先级或信息类告警，默认记录台账和趋势。

告警类型使用 PROBLEM/BUSINESS/EVENT/HOST：

- PROBLEM：故障问题类，例如接口错误、服务不可用、依赖超时。
- BUSINESS：业务指标类，例如订单量、支付成功率、券核销率、GMV、转化率异常。
- EVENT：事件或变更类，例如发布、配置变更、扩缩容、定时任务、依赖切换。
- HOST：主机或基础设施类，例如 CPU、内存、磁盘、网络、容器重启、节点异常。

后续可以扩展 SECURITY 和 DATA。SECURITY 用于安全告警，DATA 用于数据链路、指标口径和数据质量告警。

具体 case 可以这样理解：

| 优先级 | 中文解释   | 典型 case                                                      | 系统动作                                                              |
| ------ | ---------- | -------------------------------------------------------------- | --------------------------------------------------------------------- |
| P0     | 最高优先级 | 到店餐饮下单、支付、核销核心链路大面积不可用，错误率超过 60%。 | 立即创建 Incident，通知值班群和负责人，生成证据链报告，建议拉起应急。 |
| P1     | 高优先级   | 团购券核销接口失败率 20%，多个城市商家反馈无法核销。           | 创建 Incident，发送通知，查日志、Trace、代码和业务大盘。              |
| P2     | 中优先级   | 优惠券同步任务延迟 30 分钟，暂未确认用户侧影响。               | 创建 Incident，进入值班处理队列，优先查任务和数据延迟。               |
| P3     | 低优先级   | 单个容器重启一次后恢复，业务指标未异常。                       | 记录台账和趋势，默认不打扰值班人员。                                  |

| 类型     | 中文解释        | 输入示例                                                      | 处理链路                                                     |
| -------- | --------------- | ------------------------------------------------------------- | ------------------------------------------------------------ |
| PROBLEM  | 问题/故障类     | `P1 问题告警：coupon-service 核销接口 NullPointerException` | 查日志 -> 查 Trace -> 看代码 -> 查服务大盘 -> 检索 Runbook。 |
| BUSINESS | 业务指标类      | `P0 业务告警：支付成功率从 98% 降到 65%`                    | 查业务大盘 -> 查指标口径 -> 查上下游漏斗 -> 通知业务 Owner。 |
| EVENT    | 事件/变更类     | `P1 事件告警：发布后订单创建错误率升高`                     | 关联发布/配置/扩缩容 -> 判断影响 -> 建议回滚或暂停变更。     |
| HOST     | 主机/基础设施类 | `P1 主机告警：磁盘使用率 96%，日志写入失败风险`             | 查 CPU/内存/磁盘/网络 -> 定位进程/容器 -> 摘流或迁移。       |

### 2.2 SSE 流式对话

SSE 是 Server-Sent Events，适合让前端逐步展示模型输出。参考项目里有流式聊天能力；EvoHarnessAlert 当前主链路是结构化告警接入，前端主要展示告警和事件列表。生产扩展时，可以增加“对话式研判入口”：值班人员把告警文本贴进输入框，后端通过 SSE 持续返回 Agent 分析过程，例如“正在查日志”“正在匹配 Runbook”“正在生成证据链报告”。

### 2.3 MCP

MCP 可以理解为给模型和 Agent 使用的标准化工具协议。普通 HTTP API 通常是业务系统之间调用，而 MCP 更强调“让 Agent 知道有哪些工具、工具参数是什么、返回结果怎么解释”。

在告警场景里，MCP 很适合连接：

- 日志查询工具
- 监控大盘查询工具
- 代码仓库检索工具
- 发布平台
- 工单系统
- 自动化运维工具
- 通知工具

当前代码中的 MCP 工具服务位于 `app/mcp_tools/server.py`，已提供告警台账写入和通知发送工具。日志、监控、代码、发布工具属于生产扩展方向。

### 2.4 OpenAI-compatible API 与 Ollama

项目通过 `app/services/ai.py` 抽象模型调用，支持 Mock、Ollama 和 OpenAI-compatible API。OpenAI-compatible API 的好处是不同供应商只要兼容 Chat Completions 格式，就可以复用同一套调用方式。Ollama 适合本地模型演示和私有化部署验证。

### 2.5 微调大模型

微调大模型可以用于告警摘要、日志解释、Runbook 生成、接口文档问答和 rerank。当前项目提供 `models/evoharness-alert-qwen2.5-7b/Modelfile`，系统提示词已经面向 SRE、运维和值班工程师。

需要注意：微调模型不能直接获得执行生产变更的权限。对于扩缩容、回滚、重启实例、提交 PR、自动部署等动作，应该保留人工确认、权限审计和回滚机制。

## 3. 业务背景

真实生产环境里，告警规则通常不是 Agent 临时判断出来的，而是 SRE、运维或研发团队提前在监控平台配置好的。比如某个服务 5xx 超过阈值、P99 延迟超过阈值、数据库连接池耗尽、主机磁盘达到 90%、订单支付成功率下降等。

告警触发后，真正困难的是后处理：

- 这条告警是 P0、P1、P2 还是 P3？
- 是 Problem、Business、Event 还是 Host？
- 影响哪个服务、哪个接口、哪个业务域？
- 告警是否和已有 Incident 重复？
- 是否有最近发布、配置变更、主机异常或依赖异常？
- 日志里有没有强证据？
- 代码里有没有可能相关的模块？
- 大盘指标是否支持这个判断？
- 知识库里是否有 SOP、Runbook、名词解释或历史故障复盘？

以 PROBLEM 告警为例，理想链路是：查日志 -> 看代码 -> 查大盘 -> 检索企业知识库 -> 生成证据链报告。很多告警文本并不完整，日志也可能没有打全，所以仅靠告警标题很容易误判。多 Agent 的价值在于让不同角色围绕同一个黑板补齐证据，而不是让一个模型凭感觉猜根因。

项目最终希望给值班人员推送一份报告，报告必须包含强引用证据链，例如告警原文、labels、annotations、日志片段、指标截图或指标值、代码位置、Runbook 引用和历史相似事件。更进一步，在证据足够强且人工确认后，可以接自动化运维工具：扩容、重启、回滚、提交修复 PR、跑测试、部署和复盘。

## 4. 项目流程

### 4.1 前端流程

当前前端是中文控制台，入口为 `app/static/index.html` 和 `app/static/admin.html`。用户登录后可以看到总告警、未恢复告警、P0/P1/P2 事件、通知记录、队列任务、死信任务和 Agent 研判。控制台也提供了一个测试告警投递表单，可以选择 P0/P1/P2/P3 和 Problem/Business/Event/Host。

生产扩展时，可以加入“对话式告警研判”输入框：值班人员粘贴告警文本，系统通过 SSE 返回多 Agent 分析过程，并在页面上逐步展示证据链。

### 4.2 接入流程

当前已实现的接入链路：

```text
Webhook / Prometheus
-> AlertWebhookRequest
-> normalize_severity(P0/P1/P2/P3)
-> normalize_alert_type(PROBLEM/BUSINESS/EVENT/HOST)
-> AlertEvent 入 MySQL
-> Incident 聚合
-> AgentRunTrace 写入
-> ToolJob 队列
-> Ledger / Notification
```

流程图：

```mermaid
flowchart TD
    A[前端粘贴告警 / 监控系统 Webhook] --> B[FastAPI 路由]
    B --> C[AlertIngestService 告警接入]
    C --> D[优先级归一 P0/P1/P2/P3]
    C --> E[类型识别 PROBLEM/BUSINESS/EVENT/HOST]
    D --> F[AlertEvent 写入 MySQL]
    E --> F
    F --> G{是否 P0/P1/P2}
    G -- 是 --> H[Incident 聚合/更新]
    G -- 否 P3 --> I[只写台账和趋势]
    H --> J[AgentRunTrace 记录证据链]
    I --> J
    J --> K[ToolJob 异步队列]
    K --> L[LedgerRecord 台账]
    K --> M{是否 P0/P1}
    M -- 是 --> N[NotificationRecord 通知值班人员]
    M -- 否 --> O[后台控制台展示]
    N --> O
    L --> O
```

通用 Webhook：

```bash
curl -X POST http://127.0.0.1:8080/api/alerts/webhook   -H 'Content-Type: application/json'   -d '{"source":"manual","title":"API 错误率过高","severity":"P1","alertType":"problem","labels":{"service":"checkout-api","instance":"pod-1"},"annotations":{"runbook":"检查错误率、最近发布和依赖状态。"}}'
```

Prometheus Alertmanager：

```bash
curl -X POST http://127.0.0.1:8080/api/alerts/prometheus   -H 'Content-Type: application/json'   -d '{"status":"firing","alerts":[{"status":"firing","labels":{"alertname":"HighErrorRate","severity":"P1","alert_type":"problem","service":"checkout-api","instance":"pod-1"},"annotations":{"summary":"API 错误率过高","description":"5xx 超过阈值"},"fingerprint":"demo-p1-1"}]}'
```

### 4.3 动态路由

动态路由不是简单判断“要不要回答”，而是根据告警类型选择处理链路：

- PROBLEM：日志查询、代码分析、指标大盘、知识库、证据链报告。
- BUSINESS：业务指标、数据链路、指标口径、业务 Owner、历史波动。
- EVENT：发布、配置变更、扩缩容、定时任务、影响验证。
- HOST：CPU、内存、磁盘、网络、进程、容器、节点健康。

当前代码在 `app/services/alerting.py` 的 `handling_steps()` 中记录不同类型的 Agent 步骤；日志、代码、指标工具目前标记为“待接入工具”，知识库和 Trace 已经落库。

### 4.4 群机器人 / ChatOps 流程

生产里很多告警不是从后台页面发起的，而是人在值班群里粘贴一段原始告警文本并 @群机器人。这个入口可以理解为 ChatOps，也就是“在聊天工具里完成运维动作”。当前代码已经有 Webhook 和通知记录能力，群机器人属于生产扩展方向，推荐链路是：

```mermaid
sequenceDiagram
    participant G as 值班群
    participant B as 群机器人
    participant API as EvoHarnessAlert Webhook
    participant H as Alert Harness
    participant R as Agent Runtime
    participant Q as Tool Queue

    G->>B: @机器人 + 原始告警文本
    B->>API: 转成结构化 webhook payload
    API->>H: 脱敏、标准化、定级、识别类型
    H->>R: 触发多 Agent 研判
    R->>H: 返回证据链报告和建议动作
    H->>Q: 入队通知/台账/事件同步
    Q->>B: 回传中文值班报告
    B->>G: 群内展示影响、证据、建议和负责人
```

这样值班人员可以继续留在工作群里协作，不一定要打开前端页面。前端适合看全局态势和历史审计，群机器人适合 P0/P1 的即时协同。

## 5. 技术栈

```text
语言：Python
Web 框架：FastAPI / Uvicorn
关系型数据库：MySQL 8.x / SQLAlchemy ORM / PyMySQL
缓存与短期状态：Redis 7.x
配置管理：pydantic-settings / .env
AI 接入：Mock / Ollama / OpenAI-compatible API
Agent 编排：事件驱动黑板式多 Agent
RAG：Markdown Runbook / BM25 / Chroma 可选 / Embedding / rerank
MCP：mcp FastMCP 工具服务
通知：log 模式、SMTP 邮件；生产扩展可接 IM 群机器人、电话、工单
台账：openpyxl 写 Excel
前端：原生 HTML / CSS / JavaScript
部署：Docker Compose
认证：Basic Auth
```

项目运行环境按 MySQL + Redis 设计，不把 SQLite 作为开发或生产数据库。

快速启动：

```bash
cp .env.example .env
docker compose up -d mysql redis
pip install -r requirements.txt
./scripts/run-dev.sh
```

完整 Docker Compose：

```bash
docker compose up -d --build
```

默认账号：

```text
admin / admin123
viewer / viewer123
```

### 5.1 全局配置与并发估算

关键配置在 `app/core/config.py` 和 `.env.example` 中：

| 配置项                                | 中文含义                 | 当前默认                                                               |
| ------------------------------------- | ------------------------ | ---------------------------------------------------------------------- |
| `DATABASE_URL`                      | MySQL 连接串             | `mysql+pymysql://evoalert:evoalert@127.0.0.1:13306/evoharness_alert` |
| `REDIS_URL`                         | Redis 连接串             | `redis://127.0.0.1:16379/0`                                          |
| `AI_PROVIDER`                       | 大模型供应商             | `mock`，可切换 `ollama` 或 `openai`                              |
| `OLLAMA_MODEL`                      | 本地模型名               | `evoharness-alert-qwen2.5-7b:latest`                                 |
| `OPENAI_MODEL`                      | OpenAI-compatible 模型名 | `gpt-4o-mini`                                                        |
| `KNOWLEDGE_CHUNK_SIZE`              | 知识库切片长度           | 512 字符                                                               |
| `KNOWLEDGE_CHUNK_OVERLAP`           | 切片重叠长度             | 64 字符                                                                |
| `REDIS_MEMORY_TTL_SECONDS`          | Redis 短期记忆过期时间   | 86400 秒，即 1 天                                                      |
| `TOOL_QUEUE_BATCH_SIZE`             | 队列每轮拉取任务数       | 10                                                                     |
| `TOOL_QUEUE_MAX_ATTEMPTS`           | 工具任务最大重试次数     | 3                                                                      |
| `ALERT_EMAIL_RATE_LIMIT_PER_MINUTE` | 通知限流                 | 每分钟 30 条                                                           |

对于七八十个人同时在线的现实场景，可以按“控制台查询 + 少量告警投递 + 异步工具执行”估算。告警接入本身是轻请求，主要压力在数据库写入、RAG 检索和大模型调用。建议生产部署时：

- FastAPI 使用 2 到 4 个 worker 起步，后续按 CPU 和延迟扩容。
- MySQL 连接池按 20 到 50 条连接起步，避免每个请求无限制创建连接。
- Redis 用于短期状态和热点上下文，TTL 默认 1 天；P0/P1 事件可延长到 7 天。
- 大模型调用做并发限制，例如每个用户 1 到 2 个并发研判，全局按供应商限流。
- P0/P1 通知走队列，避免告警接入被邮件或 IM 发送阻塞。

上下文窗口不是越大越好。即使接 Kimi K2 这类长上下文模型，也建议把一轮报告控制在 1 万到 2 万 tokens 内：告警原文 500，日志 4000，代码 6000，指标 1500，Runbook 3000，历史事件 2000，Prompt 与输出格式 1000。这样更可控，也更接近线上成本约束。

Token 和字符的换算参考（粗略工程口径，实际以各家 tokenizer 为准）：

| 内容类型 | 经验换算 | 例子 |
| --- | --- | --- |
| 中文文本 | 1 个汉字 ≈ 0.6 - 0.75 tokens；工程保守估算按 1 汉字 ≈ 0.75 tokens，即 1 token ≈ 1.3 个汉字 | "核销接口失败率升高"（9 字）≈ 7 tokens |
| 英文/代码 | 1 token ≈ 4 个字符 ≈ 0.75 个英文单词 | `NullPointerException`（20 字符）≈ 4 - 6 tokens |
| 中英混合（日志/告警常见） | 1 token ≈ 1 个汉字或 4 个英文字符，粗略按 1 token ≈ 1.5 字符 | 一条 600 字符的日志 ≈ 400 tokens |
| 数字/标点/JSON 结构 | 密度高，1 字符 ≈ 0.3 - 0.5 tokens | JSON labels 一段 200 字符 ≈ 80 tokens |

为什么能省？中文里常用词组（"告警""核销""失败率"）会被 BPE 合并成 1 - 2 个 token，而不是按字切分；代码里的常见标识符和缩进模式也有高压缩比。所以"500 tokens 的告警原文"约等于 650 - 700 个汉字，装得下完整的标题、描述和关键 labels。

## 6. 多 Agent 协作

一句话说明：告警进入系统后，系统把标准化后的告警、上下文、任务和证据写入共享黑板，不同 Agent 按能力认领任务并产出 artifact，最后由协调者收敛成一份值班报告。

当前工程里，完整的事件驱动多 Agent 底层在第一版中以 Trace 形式落地，核心业务入口在 `app/services/alerting.py`。代码会记录以下角色：

- CoordinatorAgent：接收告警，创建研判任务，把告警写入黑板。
- TriageAgent：确认 P0/P1/P2/P3 优先级和告警状态。
- CorrelationAgent：根据 fingerprint、service、alert_type 聚合 Incident。
- ContextAgent / KnowledgeAgent：检索内置 Runbook、企业 SOP 和历史知识。
- ResponseAgent / EvidenceReportAgent：生成摘要、影响判断、根因线索、Runbook 建议和证据链报告。

生产扩展中的 Agent 还包括：

- LogQueryAgent：按时间窗口、traceId、service、instance 查询日志。
- CodeAnalysisAgent：根据日志关键字、接口、模块名定位代码。
- MetricsAgent：查看错误率、延迟、饱和度、依赖状态和业务指标。
- ChangeReviewAgent：关联发布、配置、扩缩容和定时任务。
- HostDiagnosticAgent：检查 CPU、内存、磁盘、网络、进程和容器。
- SafetyAgent：防止误报升级、敏感信息泄露和未经审批的高风险动作。

Agent 之间的协作方式是黑板式：每个 Agent 不直接把全部上下文塞给下一个 Agent，而是把自己的结论、证据、置信度和待验证点写入共享状态。后续 Agent 只读取自己需要的部分，减少上下文污染。

为什么不用一个 Agent 全做？因为告警研判涉及日志、代码、指标、知识库、发布、主机和通知，职责过多会导致 prompt 巨大、证据混乱、难以审计，也很难限制高风险工具权限。拆成多个 Agent 后，每个 Agent 的输入、输出、权限和模型都可以单独控制。

默认事件驱动链路可以这样理解：入口先做**确定性归一**（正则/规则提取 intent、priority、alert_type，发布到黑板，不占用 Agent 认领预算），`CoordinatorAgent` 根据归一结果判断是否需要 Context；需要时由 `ContextAgent` 准备 Memory、RAG 和 Skill，`ResponseAgent` 生成候选回复，`SafetyAgent` 再审查候选回复，最后由 `CoordinatorAgent` 采纳最终输出。

> 设计取舍：意图提取、优先级归一、风险评估这类"规则就能确定性完成"的步骤，不包装成 Agent，也不消耗 claim 预算。Agent 预算只花在真正需要证据收集与模型判断的任务上（上下文准备、候选回复生成、安全审查）。

ContextAgent 不是每轮都执行。普通闲聊、系统能力介绍、接口怎么用、名词解释这类请求仍会做意图判断和安全评估，但不会强行注入企业 RAG 和长历史。只有当 intent 明确是告警研判，或者 risk/priority 达到 Medium 及以上，或者任务显式要求上下文能力时，Coordinator 才会创建 Context 任务。这样可以避免把普通问答“告警化”，也能控制成本和延迟。

ContextAgent 运行时的推荐步骤是：

```text
load_history
-> 优先读 Redis 最近 40 条
-> Redis miss 时从 MySQL ChatMessage 倒序读取最近 40 条
-> 反转成正常对话顺序
-> 脱敏后 replace 回 Redis
-> compact_history_for_prompt 生成压缩历史和确定性摘要
-> 尝试用模型生成 1-3 条中文记忆要点
-> 摘要失败时回退到确定性摘要
-> 根据 intent/risk 决定是否检索知识库和加载 Skill Context
-> 产出 context artifact
```

Context artifact 可以包含：`memory_brief`、`model_history`、`knowledge_query`、`retrieved_knowledge`、`skill_context`、`primary_memory_key`。后面的 ResponseAgent 会读取这个 artifact，把记忆摘要、知识片段和 Skill 指引合进候选回复 prompt。

## 7. 事件驱动 Runtime

### 7.1 Runtime 的入口是 Factory

当前代码里的 Runtime 入口是 `app/agents/factory.py`。Factory 负责屏蔽具体 Runtime 实现，调用方只需要调用 `create_agent_runtime(db, settings)`，就能拿到 `EventDrivenAgentRuntimeService`。

代码结构可以理解成：

```text
app/agents/factory.py
-> from app.agents.event_driven_runtime import EventDrivenAgentRuntimeService
-> return EventDrivenAgentRuntimeService(db=db, settings=settings)
```

默认配置在 `app/core/config.py`：

| 配置                                   | 中文含义                     | 默认值                       |
| -------------------------------------- | ---------------------------- | ---------------------------- |
| `AGENT_FRAMEWORK`                    | Agent 框架类型               | `event_driven_multi_agent` |
| `AGENT_RUNTIME_MAX_STEPS`            | Coordinator 最大轮数         | `8`                        |
| `AGENT_RUNTIME_MAX_CLAIMS_PER_ROUND` | 每轮最多选择多少个 claim     | `4`                        |
| `AGENT_RUNTIME_MAX_CLAIMS_PER_AGENT` | 同一个 Agent 最多 claim 几次 | `3`                        |
| `AGENT_FINAL_ACCEPT_MIN_CONFIDENCE`  | 最终采纳的最低置信度         | `0.6`                      |

这些值决定 Runtime 的边界：它不是无限循环。Coordinator 最多跑 8 轮，每轮最多选择 4 个 claim，同一个 Agent 最多 claim 3 次。候选回复的 confidence 低于 0.6 时，即使 SafetyAgent 审查通过，也不应该被最终采纳。当前 `/api/agent/status` 会暴露这些预算配置，方便后台确认运行边界。

### 7.2 一轮请求从哪里进入 Runtime

当前已经实现的生产主入口是告警接入接口：

- `POST /api/alerts/webhook`
- `POST /api/alerts/prometheus`

这条链路进入 `AlertIngestService`，完成脱敏、标准化、Incident 聚合、AgentRunTrace 和 ToolJob。也就是说，当前第一版的“Alert Agent Handler / Harness”主要收敛在 `app/services/alerting.py`。

对话式入口属于生产扩展方向，推荐接口是 `POST /api/chat/stream`。它适合前端或群机器人把一段告警文本贴进来，然后通过 SSE 逐 token 返回报告。推荐链路如下：

```text
POST /api/chat/stream
-> ChatService.stream_chat
-> AlertAgentHandler.run
-> create_agent_runtime(db, settings).run
-> EventDrivenCoordinator.run_board
-> AgentRunResult
-> AIClient.stream_response_messages
-> SSE token 下发
-> 保存助手消息
-> 派发 Tool Plan
-> SSE done
```

这里要注意边界：ChatService 不负责判断意图，不负责创建告警报告，也不直接写台账或发送预警。它主要做三件事：调用 Handler、拿到 Runtime 准备好的 response messages、用 AIClient stream 把最终文本逐 token 发给前端。

真正的一轮业务变换发生在 AlertAgentHandler / AlertAgentHarness：输入脱敏、会话解析、调用 Runtime、保存用户消息、生成告警报告、写入 Trace、生成工具计划。这样 HTTP 层很薄，Runtime 也不用关心数据库报告怎么建、工具任务怎么派发。

### 7.3 一轮请求进入 Runtime 的逻辑图

```mermaid
sequenceDiagram
    participant U as 用户/监控系统/群机器人
    participant API as FastAPI Routes
    participant H as AlertAgentHandler / Harness
    participant F as Runtime Factory
    participant R as EventDrivenAgentRuntimeService
    participant C as EventDrivenCoordinator
    participant BB as Blackboard 黑板
    participant DB as MySQL
    participant Q as Tool Queue

    U->>API: 提交结构化告警或对话式告警文本
    API->>H: 调用告警 Handler
    H->>H: 脱敏、会话解析、权限边界
    H->>F: create_agent_runtime(db, settings)
    F->>R: 返回 EventDrivenAgentRuntimeService
    R->>BB: 创建 turn_id/user_id/session_id/model_input/root task
    R->>C: run_board
    C->>BB: 创建任务、收集 claim、控制预算
    BB->>C: 返回 artifacts/events/tasks
    C->>R: AgentRunResult
    R->>H: 返回 response messages 和黑板过程
    H->>DB: 保存 AlertEvent/Incident/AgentRunTrace/消息
    H->>Q: 生成 Ledger/Notification/Incident 工具计划
    H->>API: 返回 JSON 或 SSE token
```

### 7.4 Harness 与 Runtime 边界

推荐边界是：Harness 管业务接入，Runtime 管 Agent 协作。

Harness 负责这些确定性的工程动作：接收原始输入、保存原始告警、脱敏得到 `model_input`、校验用户/团队/会话权限、标准化字段、创建或复用 Incident、保存 AgentRunTrace、生成 ToolJob、把最终结果返回 API 或群机器人。

Runtime 负责这些智能协作动作：创建本轮黑板、把告警事实拆成任务、让 Agent 认领任务、合并 artifact、记录 events/tasks/artifacts、产出最终报告草稿。

更贴近生产的一轮请求顺序如下：

```mermaid
flowchart TD
    A[API / 群机器人收到原始告警] --> B[Harness 保存原始 AlertEvent]
    B --> C[Harness 脱敏生成 model_input]
    C --> D{是否带 sessionId}
    D -->|有| E[校验 session 是否属于当前用户/团队]
    D -->|无| F[创建 ChatSession publicId=UUID.hex 标题取前36字符]
    E --> G[Runtime 初始化]
    F --> G
    G --> H[创建 Blackboard: turn_id/user_id/session_id/original_input/model_input]
    H --> I[Coordinator 创建 Root Task]
    I --> J[Understanding/Triage 识别意图、P0-P3、类型]
    I --> K[Safety 脱敏检查和高风险动作门控]
    I --> L[Context 检索 Memory、Runbook、历史事件]
    I --> M[Response 生成候选报告]
    J --> N[Runtime 返回 AgentRunResult]
    K --> N
    L --> N
    M --> N
    N --> O[Harness 保存 AgentRunTrace 和 Incident]
    O --> P[Harness 生成 ToolJob: 台账/通知/事件同步]
    P --> Q[返回前端或群机器人]
```

在多租户场景里，还需要把 `tenant_id/team_id` 放进用户、会话、告警、Incident 和工具调用审计里。到店餐饮值班组只处理自己负责的团购、订单、支付、发券和核销服务；如果 Trace 发现是上游或下游团队的问题，系统应该生成“跨团队同步建议”，推送到协作群或工单，而不是让本组越权处理别人的服务。

### 7.5 黑板 / Runtime 的核心状态

黑板定义在 `app/agents/events.py`，类名是 `CollaborationBlackboard`。它保存 `tasks`、`messages`、`artifacts`、`events` 和 `final_artifact_id`。这套 Runtime 里 Agent 不是互相调用对方方法，而是通过黑板协作：一个 Agent 想做事，先 claim 一个 task；做完后返回 `AgentTurnResult`，里面可以包含 messages、artifacts、tasks、events 和 close_task；黑板再把执行结果整合进去。

核心数据结构：

| 数据结构            | 中文解释                | 例子                                                         |
| ------------------- | ----------------------- | ------------------------------------------------------------ |
| `AgentTask`       | 待办事项                | 理解输入、评估风险、准备上下文、生成候选回复、审查候选回复。 |
| `AgentArtifact`   | 结构化产物              | Intent、Risk、Context、Response Proposal、Safety Review。    |
| `AgentMessage`    | Agent 协作消息          | ResponseAgent 请求 SafetyAgent 审查候选回复。                |
| `AgentEvent`      | 协作事件                | 任务创建、任务认领、Artifact 发布、安全覆盖、最终采纳。      |
| `AgentTurnResult` | 单个 Agent 一次执行结果 | 本轮新增的消息、产物、任务和是否关闭当前任务。               |

典型 artifact：Coordinator 归一化后直接发布确定性的 `intent` 与 `risk`（owner=CoordinatorAgent，normalizedBy=rule），ContextAgent 发布 `context`，ResponseAgent 发布 `response_proposal`，SafetyAgent 审查后发布 `safety_review`。

黑板方法大多返回新对象，不直接修改旧对象。例如 `add_task()`、`append_event()`、`add_artifact()` 都会返回新的 `CollaborationBlackboard`。这样 Trace 看到的是一串真实发生过的协作记录，而不是一个被不断覆盖的状态对象。测试里也覆盖了这一点：旧 board 不会因为新 task、event 或 artifact 被改掉。

### 7.6 Coordinator 怎么调度

调度逻辑在 `app/agents/coordinator.py` 的 `EventDrivenCoordinator`。每轮开始时，Coordinator 会保证 Root Task 存在；如果输入命中 P0/P1 这类高优先级告警，Root Task 会是更高优先级。之后每一轮按这个节奏推进：

```text
追加 ROUND_STARTED
-> derive_missing_work 派生缺失任务
-> 尝试执行可认领任务
-> 合并 AgentTurnResult
-> 再次尝试最终采纳
-> 达到最终采纳或耗尽轮数预算后结束
```

`derive_missing_work` 会看黑板上缺哪些 artifact，然后补对应任务。注意：`intent` 和 `risk` 由 `publish_normalized_facts()` 在每轮开始前以确定性规则直接发布，不再是 Agent 任务：

| 缺失内容                   | 派生任务                 | 所需能力      | 期望产物              |
| -------------------------- | ------------------------ | ------------- | --------------------- |
| intent/risk（确定性发布，无任务） | —（publish_normalized_facts） | —（规则）     | `intent` / `risk`   |
| intent/risk 表明需要上下文 | `task_gather_context`  | CONTEXT       | `context`           |
| intent/risk 已具备         | `task_response`        | RESPONSE      | `response_proposal` |
| 新候选回复未审查           | `task_review_response` | SAFETY        | `safety_review`     |

最终采纳不是 ResponseAgent 说了算。Coordinator 只有在这些条件同时成立时才会调用 `accept_final()`：存在最新 `response_proposal`；存在指向该 proposal 的 `safety_review`；`safety_review.approved=true`；`response_proposal.confidence >= AGENT_FINAL_ACCEPT_MIN_CONFIDENCE`。当前默认阈值是 0.6，轻量 Runtime 中 ResponseAgent 候选回复 confidence 是 0.86，SafetyAgent 审查 confidence 是 0.95，所以审查通过时通常可以被采纳。

### 7.7 Claim-based Scheduler

Claim-based Scheduler 的核心思想是“让最合适的 Agent 认领当前任务”。在 `AgentRegistry` 中，系统会遍历剩余 task，先按 required capability 过滤 Agent，再调用 `agent.decide(task, board)`。如果 `AgentDecision.claim=true`，它就进入候选列表，候选列表按 confidence 从高到低排序。

Coordinator 还会综合 task priority、decision confidence 和 agent 名称排序。Runtime 不是固定流水线，不是永远先归一再 Context 再 Response；真实顺序由任务和 claim 决定。因为 intent 和 risk 在每轮开始前就由确定性规则发布好了，ContextAgent 可以在第一轮就加入告警研判；ResponseAgent 等 intent 和 risk 就绪后提出候选回复，SafetyAgent 最后审查。

调度还有预算限制：每轮最多 `AGENT_RUNTIME_MAX_CLAIMS_PER_ROUND=4` 个 claim，同一个 Agent 整个运行最多 `AGENT_RUNTIME_MAX_CLAIMS_PER_AGENT=3` 次，避免某个 Agent 一直认为自己有事做，占满整个 Runtime。

这和普通单 Agent 最大区别在于：单 Agent 往往串行思考和工具调用，而告警场景需要多证据并行补齐、交叉验证和审计留痕。Runtime 有轮数和 claim 次数限制，避免无限循环。

### 7.8 普通聊天和告警链路的区别

普通聊天追求自然回答；告警链路追求事实、证据、可执行动作和责任边界。普通 Chat 不会进入 RAG，不会生成告警报告，也不会触发后台工具队列。告警链路不能只输出“可能是数据库问题”，而要说明依据是什么、还缺什么证据、下一步查哪里、是否需要升级、是否允许自动执行。

### 7.9 Trace 链路和代码 Agent

真实链路里，一次请求可能穿过上百个微服务，值班人员通常只负责其中几个服务。因此告警报告必须把 `traceId`、入口接口、上下游服务、异常 span、错误日志和代码模块串起来。Trace 的作用不是“多一个字段”，而是帮助判断问题到底在本组、上游、下游还是基础设施。

日志和代码要结合看，原因也很现实：日志经常打不全，异常栈可能只显示某一层包装错误。生产扩展中的 CodeAnalysisAgent 可以这样落地：

1. 根据服务名和权限拿到仓库地址。
2. 在隔离沙箱或复用的 Docker workspace 中 clone 仓库。
3. 首次初始化索引可能需要 1 到 2 分钟，后续按 commit 缓存复用。
4. 用 BM25/符号索引/AST/调用图定位接口、类、方法和异常栈。
5. 把代码片段作为证据写回黑板，不直接让模型改生产代码。
6. 如果建议修复，需要人工确认后再生成 PR，并走 CI、Code Review、灰度和回滚预案。

### 7.10 为什么 Runtime 适合 Alert

Alert 系统天然是事件驱动的：告警触发、事件聚合、工具查询、通知发送、人工确认、自动修复、复盘更新，每一步都是事件。黑板式 Runtime 可以把这些事件组织起来，让系统既能自动化推进，也能留下完整审计链。

## 8. Harness

Alert Agent Harness 的作用是把外部不稳定输入变成系统内部稳定流程。它需要完成：

- 接收告警文本或结构化 payload
- 脱敏和标准化
- 生成 fingerprint
- 判断 P0/P1/P2/P3
- 判断 PROBLEM/BUSINESS/EVENT/HOST
- 写入 AlertEvent
- 聚合或更新 Incident
- 调用 Runtime 或静态 Agent 步骤
- 保存 AgentRunTrace
- 生成 ToolJob

当前代码中，`app/services/alerting.py` 承担了第一版 Alert Agent Harness 的职责。`AgentRunTrace` 保存每个 Agent 的动作、证据和结论。`app/harness/runner.py` 是 Engineering Harness，用于工程检查和测试回归。

两者区别：Alert Agent Harness 面向线上业务请求，Engineering Harness 面向研发验收。前者处理生产告警，后者验证项目是否还能正常运行。

## 9. 上下文管理与上下文压缩

告警系统不能把所有历史、所有日志、所有代码都塞给模型。合理做法是分层上下文：

- L0 当前告警：title、description、severity、alertType、labels、annotations。
- L1 当前事件：Incident、状态、负责人、历史关联告警。
- L2 工具证据：日志片段、指标点、代码引用、发布记录。
- L3 知识库：Runbook、SOP、接口文档、名词解释、历史故障复盘。
- L4 Agent 私有记忆：每个 Agent 自己的中间判断和偏好。
- L5 Trace：最终用于审计和回放的证据链。

一轮请求中的上下文流向：API 接收 payload，AlertIngestService 标准化字段并入库，Context/Knownledge 相关逻辑检索 Runbook，AgentRunTrace 保存中间步骤，Tool Queue 根据结果触发台账和通知。

上下文压缩不是简单摘要，而是保留关键事实：时间窗口、服务名、实例、接口、错误码、异常指标、代码路径、Runbook 引用、日志片段和缺失证据。

历史必须压缩，是因为告警系统最容易犯的错误就是把历史越塞越长。刚开始看不出问题，但会话多了以后，prompt 会变慢、变贵，还会带来干扰。比如同一个 session 里，用户上周问过 PROBLEM 问题告警，今天又问 EVENT 发布告警；如果旧告警一直放在最显眼的位置，模型可能把今天的普通问题误判成旧故障延续。相反，如果完全不看历史，用户连续发送多条同一事件的告警时，系统又会像第一次见面一样生硬，无法承接上下文。

EvoHarnessAlert 的做法是“旧历史压成内部摘要，最近消息保留脱敏原文”。摘要负责延续背景，最近原文负责保留细节和风险线索。`compact_history_for_prompt()` 会先脱敏历史，再调用 `summarize_history_for_memory()` 生成确定性摘要；摘要规则尽量不用模型兜底，避免模型摘要失败导致上下文链路不可用。

确定性摘要默认包含：

- 用户近期关注：最近 4 条用户消息，每条最多 80 个字。
- 已给过的建议：最近 3 条助手回复，每条最多 70 个字。
- 本轮输入关注：当前输入最多 80 个字。
- 最终摘要默认最多 500 个字。

如果历史消息不超过最近消息阈值，系统保留这些脱敏后的原文。如果超过阈值，系统会生成一条内部 `system` 消息，再拼接最近 8 条脱敏原文。内部 system 消息会明确说明：历史摘要仅供 EvoHarnessAlert 内部上下文使用，不要向值班人员或客户展示，不要据此输出后台标签、风险等级或未经验证的根因。Memory 在这里不是用户画像，它只是本轮判断的辅助材料。

为什么不用全量历史？因为成本高、噪声大、容易把旧事件误当成当前根因。为什么不用纯滑动窗口？因为关键证据可能不在最近几条里。为什么不用纯摘要？因为摘要会丢失强引用证据。正确做法是“结构化事实 + 证据片段 + 必要摘要 + 最近脱敏原文”。

MySQL 和 Redis 都要用：MySQL 存事实、审计、事件、任务和报告；Redis 适合存短期上下文、热状态、会话进度和 Agent 私有临时记忆。Trace 在上下文管理中用于回放和复盘，也能反向优化 Prompt、Skill 和 Runbook。

## 10. Memory

Memory 可以翻译成“记忆”或“上下文记忆”。在 EvoHarnessAlert 里，它不是用户画像，也不是风险标签仓库，而是为了让下一轮告警研判能复用最近上下文。

当前 MySQL 表保存长期事实和审计：

- AlertEvent：原始告警事实。
- Incident：聚合后的故障事件。
- AgentRunTrace：Agent 研判过程。
- ToolJob：异步工具任务。
- NotificationRecord：通知记录。
- LedgerRecord：台账记录。
- KnowledgeChunk：知识库切分结果。

Redis 保存短期记忆，当前代码落在 `app/services/memory.py` 的 `RedisShortTermMemoryStore`。接口刻意保持很少：

| 接口                          | 中文含义     | 作用                                                                |
| ----------------------------- | ------------ | ------------------------------------------------------------------- |
| `append`                    | 追加消息     | 用`rpush` 把一条脱敏消息追加到 Redis list。                       |
| `load_recent`               | 读取最近消息 | 从 Redis list 读取最近 N 条，默认 40 条。                           |
| `replace`                   | 整批替换     | 从 MySQL 历史消息恢复最近 40 条后，整批写回 Redis。                 |
| `messages_from_owner_roles` | 按角色读取   | 只取指定 owner/session 下某些 role 的消息，例如 user 和 assistant。 |

每条 Redis 消息结构非常简单：

```json
{
  "role": "user",
  "content": "脱敏后的消息内容",
  "createdAt": "2026-09-01T12:00:00"
}
```

这里不保存额外画像，也不把诊断标签、风险等级、内部评分塞进短期记忆。短期记忆只保留对话或告警研判内容本身，而且内容会被脱敏。

`append` 的写入流程是：

```text
输入 owner/session/role/content
-> PrivacySanitizer 脱敏
-> rpush 追加到 Redis list
-> ltrim 裁剪到最近 40 条
-> expire 设置 86400 秒过期
```

这样设计有两个好处：第一，短期记忆不会无限增长；第二，过期会话会自然释放 Redis 空间。默认长度来自 `REDIS_MEMORY_MAX_MESSAGES=40`，默认过期时间来自 `REDIS_MEMORY_TTL_SECONDS=86400`。

`load_recent` 读取时还会再做一次脱敏。它会从 Redis 读取 JSON row，解析出 `role/content/createdAt`，然后再次通过 `PrivacySanitizer` 处理内容。即使 Redis 里意外混入了未脱敏内容，读取阶段也会做一道防护。

`replace` 主要用于缓存回填：如果 Redis 没有某个 session 的短期记忆，但 MySQL 里有历史消息，系统可以从数据库取最近 40 条，转换成 AI message 后整批写回 Redis。下一轮请求就可以直接走 Redis，不必每次都查数据库。

为什么 Redis 和 MySQL 要同时用？只用 Redis 不够，因为 Redis 有 TTL，也可能因为内存策略被清理，不适合承担完整审计、报告留档和人工复盘记录。在告警场景里，后续人工接手、管理员复盘、事件追责和报告归档都需要可靠记录，所以 MySQL 必须作为事实源。

只用 MySQL 也不理想。每轮对话都从数据库拉长历史，再交给模型，会让读取、排序、脱敏和上下文构造变得很重。Redis 更适合保存最近的会话窗口，读写快，结构简单。EvoHarnessAlert 把两者分开：Redis 负责短期上下文，MySQL 负责完整业务记录；Redis miss 时从 MySQL 回填。这样既保留响应速度，也不会丢失长期留痕。

这种分层也方便部署。开发或降级环境里，Redis 不可用时主链路仍然可以跑，只是短期记忆缓存能力下降；正式环境里，Redis 提供更好的短期记忆体验，MySQL 继续负责审计和后台流程。

为什么 Agent 私有记忆要单独存在？因为不同 Agent 的中间思路不应该互相污染。LogQueryAgent 关心日志关键字和 traceId，CodeAnalysisAgent 关心代码路径和调用关系，SafetyAgent 关心敏感信息和高风险动作。如果全部混在一个上下文里，模型容易把未验证推测当事实，也会增加越权工具调用风险。

在事件驱动版本里，除了会话短期记忆，还应该有 agent private memory，也就是“Agent 私有记忆”。它不是给用户建长期画像，而是给每个 Agent 保留自己的工作记录。例如 CoordinatorAgent 记录归一化结果 `intent/priority/alert_type`，SafetyAgent 记录 `risk_summary/review_approved/reason`，ContextAgent 记录 `context_intent/risk/retrieved_count`，ResponseAgent 记录 `response_mode/intent/risk`。这些记录可以写入隔离 key，格式类似 `agent:{agent_name}:{session_id}`，并复用 `RedisShortTermMemoryStore` 的裁剪、TTL 和脱敏逻辑。

私有记忆的价值在于隔离：SafetyAgent 的风险记录不能直接变成客户端话术，ResponseAgent 的表达策略也不应该影响归一化结果的客观性。后台可以记录 risk、confidence、summary，客户端只应该看到具体告警报告、证据链和处置建议。私有记忆把这些边界留在代码里，而不是只靠 prompt 自觉。

为什么 List 要压缩？因为告警、日志、代码、指标、历史事件都可能很长，不控制长度就会让 Redis 和模型上下文一起膨胀。Redis list 只保留最近 40 条，模型输入再做二次筛选：旧历史压缩为最多 500 字的内部摘要，最近 8 条保留为脱敏原文。压缩后重点保留服务名、时间窗口、traceId、异常指标、代码路径、Runbook 引用和日志证据，丢掉重复闲聊、过期状态和无证据推测。

即使用 Kimi K2 或其他长上下文模型，也仍然需要上下文筛选。长上下文不是无限上下文，成本和延迟都要控制。一个可落地的 token 预算示例：

```text
告警原文与 labels：500 - 1,000 tokens
日志证据：2,000 - 4,000 tokens
代码片段：2,000 - 6,000 tokens
指标摘要：500 - 1,500 tokens
Runbook / SOP：1,000 - 3,000 tokens
历史相似事件：1,000 - 2,000 tokens
最终输出格式与安全约束：500 - 1,000 tokens
```

实际接入 Kimi K2 时，不建议在 README 写死实时价格。更稳妥的方式是用供应商当时的单价计算：`输入 token 数 * 输入单价 + 输出 token 数 * 输出单价`，再按每天告警量估算成本。

## 11. Agent 范式

### 11.1 总体范式

本项目采用的是事件驱动黑板式多 Agent 范式。它不是 ReAct 循环，也不是把所有能力塞进一个单 Agent 大 Prompt，而是让多个角色在受控 Runtime 里围绕同一块黑板协作。

Alert Client 对话入口由 Alert Agent Handler 统一承接。当前第一版代码里，这个职责主要落在 `app/services/alerting.py` 的 `AlertIngestService`；生产形态可以把它拆成更明确的 `AlertAgentHandler / AlertAgentHarness`。它负责输入脱敏、会话解析、调用 Agent Runtime、保存用户消息、生成告警报告、写入 Trace，并在 Client 回复之后触发工具计划。

默认 Runtime 是 `event_driven_multi_agent`，配置位于 `app/core/config.py`，也会在 `/api/agent/status` 里暴露。当前默认预算是：最多 8 轮，每轮最多 4 个 claim，每个 Agent 最多 claim 3 次。预算存在的意义是防止 Agent 无限循环，也防止某个 Agent 反复认领任务挤占其他角色。

```text
Alert Client / Webhook / 群机器人
-> Alert Agent Handler / Harness
-> 输入脱敏、会话解析、权限边界
-> Event Driven Multi Agent Runtime
-> Blackboard 黑板协作
-> AgentRunTrace 审计
-> ToolJob 工具计划
-> 前端或群机器人返回中文报告
```

### 11.2 多 Agent 分工

CoordinatorAgent 创建 Root Task，并维护任务板、预算、安全门槛、冲突仲裁和最终采纳。它不负责亲自完成所有分析，而是把任务拆给更合适的 Agent。

**确定性归一（不是 Agent）**：意图识别、优先级/告警类型归一、风险评估由 `publish_normalized_facts()` 用规则直接完成——用户输入的一段告警文本里本来就带着这些信息，正则和映射表就能提取，没必要消耗一次 Agent 认领，也不该让"Agent 决策"出现在本可确定性的地方。

SafetyAgent 独立审查候选回复是否安全：是否泄露敏感信息，是否把推测说成事实，是否建议了未经审批的回滚、重启、扩缩容、部署等高风险动作。审查涉及语义判断和上下文权衡，是真正值得 Agent 承担的环节。

ContextAgent 在需要时准备 Memory、RAG 和 Skill。它会根据 intent/risk 决定是否加载历史、是否检索知识库、是否注入告警类型对应的 Skill Context。普通 Chat 不会强行进入 RAG，也不会生成告警报告。

ResponseAgent 读取黑板上的意图、风险、上下文和安全约束，提出候选回复。最终回复必须经过 SafetyAgent 审查，并由 CoordinatorAgent 采纳。

### 11.3 为什么不是 ReAct 主导

ReAct 适合开放式工具使用：模型在 Thought、Action、Observation 中不断决定下一步。这个模式适合单 Agent 探索任务，比如“查一下日志，再想下一步”。但生产告警不能把关键决策完全交给模型自由发挥。

告警场景里，有些动作必须被框架和后台策略控制：

- 是否进入高优先级告警处理。
- 是否生成 Incident。
- 是否触发 P0/P1 通知。
- 是否调用后台工具队列。
- 是否建议自动修复、回滚、扩缩容或部署。
- 是否把内部 risk、confidence、trace、后台标签暴露给客户端。

EvoHarnessAlert 的做法是：模型只在受控任务中产出 artifact 或候选 prompt，Runtime 和后台策略负责安全门控、报告落库和工具执行。默认事件驱动 Runtime 中，所有输入都会经过独立风险评估，候选回复也要经过 SafetyAgent 审查。普通 Chat 不进入 RAG，不生成告警报告，也不触发后台工具队列。告警类请求才会进入 Incident、Trace、ToolJob 这些业务链路。

### 11.4 和单 Agent 大 Prompt 的区别

单 Agent 大 Prompt 的问题是职责混乱：同一个模型既要判断意图，又要查知识库，又要写回复，还要决定是否调用工具。prompt 会越来越长，安全边界也会越来越模糊。

黑板式多 Agent 的优点是边界清楚：确定性归一（规则，非 Agent）只负责提取和评估，SafetyAgent 只负责安全审查，ContextAgent 只负责上下文，ResponseAgent 只负责候选回复，CoordinatorAgent 只负责调度和采纳。每一步都能写入 Trace，后续复盘时可以看到 intent、risk、RAG、response、prompt 的完整过程。

这也是本项目的核心取舍：不是把工具选择权完全交给大模型，而是让模型在受控任务里产出结构化结果，再由 Runtime、Harness 和 Tool Queue 接管安全、报告和工具执行。

## 12. RAG 知识库构建

### 12.1 知识库内容

告警知识库主要维护：

- 企业 SOP
- Runbook
- 操作规范
- 名词解释
- 接口文档
- 服务依赖说明
- 历史故障复盘
- 值班手册
- 发布和回滚流程

以本地商业、团购券、酒旅等业务为例，知识库里可能包含券核销链路、订单链路、支付链路、商家侧接口、活动配置、履约状态、风控规则、结算规则等名词和接口说明。

本项目使用一套虚构的“到店餐饮团购平台”接口文档作为知识库示例。文档按常见开放平台的组织方式描述业务名词、API 列表、图片说明、参数解释、错误码和版本信息，但不引用任何真实公司的名称、链接或内部实现细节。

示例业务覆盖团购套餐、订单、支付、发券、到店核销和退款。告警出现时，RAG 不只要知道某个接口怎么调用，还要知道这个接口属于哪条业务链路、上下游是谁、错误码代表什么，以及失败可能影响商家、消费者、订单状态还是券码履约。知识库中的服务名、接口和错误码均为本项目演示约定，真实接入时应由业务团队提供并标注版本、权限和维护人。

### 12.2 文档采集和清洗

生产环境中，文档可能来自内部知识库、接口平台、代码仓库 README、SOP 文档、故障复盘、MySQL 表或 Hive 表。可以每日定时抽取一次，进入清洗流程。

清洗包括：去掉模板噪声、去掉无权限信息、敏感字段脱敏、统一标题层级、补充 metadata。metadata 建议包括业务线、服务名、接口名、文档类型、更新时间、Owner、权限级别和版本号。

### 12.3 Chunking

普通 SOP 可以按标题和段落切分。Runbook 可以按故障类型、症状、排查步骤、止血动作和恢复验证切分。接口文档建议按 endpoint 维度切：

```text
接口：POST /coupon/redeem
服务：coupon-service
用途：团购券核销
请求参数：...
响应字段：...
错误码：...
依赖服务：...
常见告警：...
排查建议：...
```

这样切的好处是检索到一个 chunk 时，模型能同时看到接口含义、参数、错误码和相关告警，而不是只拿到孤立字段。

当前代码的切分函数在 `app/services/knowledge.py` 的 `chunk_text()`，默认使用固定长度和 overlap。生产环境建议升级为“标题结构切分 + 接口语义切分 + 固定长度兜底”。

接口文档 Chunk 示例：

```text
chunk_id: api_coupon_redeem_error_codes
业务域：到店餐饮 / 团购
接口：团购券核销
服务：coupon-service
HTTP：POST /coupon/redeem
错误码：COUPON_EXPIRED、COUPON_ALREADY_USED、SHOP_NOT_MATCH
相关告警：核销失败率升高、下游券状态查询超时
排查建议：检查券状态服务、门店绑定关系、核销幂等表、最近发布
```

图片入库也要处理。很多企业文档里的图片包含流程图、时序图、页面截图、字段说明。如果直接忽略图片，RAG 会丢失关键上下文。推荐流程：

```text
文档 HTML / Markdown
-> 抽取图片 URL 和图片上下文段落
-> OCR 识别图片中文字
-> 视觉模型生成图片摘要
-> 保存 image_caption、ocr_text、source_url、page_section
-> 与相邻正文合并成多模态 Chunk
-> 文本向量化入库，原图对象存储保留引用
```

成本估算要按图片数量和模型单价实时计算，不建议写死。粗略估算方式是：`图片数量 * 单图 OCR/视觉理解成本 + 生成 token 成本`。如果一个业务域有 1000 张图片，可以先只处理流程图、接口截图、错误码图这类高价值图片；图标、装饰图、重复截图可以过滤掉。

### 12.4 Embedding 和入库

Embedding 模型可选：

- `text-embedding-3-small`：OpenAI embedding，适合通用语义检索。
- `bge-m3`：多语言检索常用，适合中文和英文混合文档。
- `m3e`：中文场景常用 embedding。
- 企业内部 embedding 模型：适合内部术语、接口名和业务名词。

相似度计算通常使用 cosine similarity。当前项目通过 `KnowledgeService`、`KnowledgeChunk` 和可选 `ChromaKnowledgeStore` 模拟企业知识库入库。生产环境中，可以替换为内部向量平台或知识库 API。

如果要在本地做一个轻量级但像样的知识库，可以用 HNSW 作为向量索引，BM25 作为关键词索引，形成双路召回：向量负责语义相似，BM25 负责接口名、错误码、类名、方法名精确匹配。召回后再接一个小模型 reranker，对“告警 Query + 候选 Chunk”进行相关性排序。

图片和截图也可以进入知识库，但要先转成文本证据。接口文档里的流程图、字段截图、错误码截图可以通过 OCR 或多模态模型生成 caption，再和原页面 URL、图片 hash、章节标题一起入库。成本估算不要写死实时价格，建议按“图片数量 x 单图 OCR/视觉模型调用成本 + 人工抽检成本”估算；对企业内部文档，优先用离线 OCR 和抽样人工校验，只有复杂图再调用视觉模型。

本地轻量方案可以采用 HNSW 向量索引 + BM25 双路召回：HNSW 负责语义相近的 SOP/复盘召回，BM25 负责接口名、错误码、类名、方法名、trace 字段等精确匹配。双路召回后交给 rerank 小模型排序，rerank 输入是“告警文本 + 候选文档片段”，输出相关性分数。当前项目用 `KnowledgeService`、`KnowledgeChunk` 和本地评测数据 mock 企业知识库；生产可替换为内部知识库/向量平台。

### 12.5 持续更新

推荐链路：

```text
内部文档 / 接口平台 / 代码仓库 / 复盘文档
-> MySQL 或 Hive 明细表
-> 每日定时任务
-> 清洗脱敏
-> Chunking
-> Embedding
-> 增量入库
-> 版本审计
-> RAG 评测
```

如果文档删除或权限变化，向量库也要同步删除或降权，避免模型引用过期或越权内容。

## 13. RAG 检索与生成

### 13.1 检索流程

RAG 检索从 Query 预处理开始。Query 不一定是用户自然语言，也可能是一条告警文本。预处理需要提取：服务名、接口名、错误码、时间窗口、告警类型、优先级、instance、env、region、owner。

之后将 Query 向量化，进入相似度检索。生产环境中通常会叠加 metadata filter，例如只检索当前业务线、当前服务、当前权限范围、最近版本的文档。

### 13.2 混合检索和重排

只用向量检索可能漏掉接口名、错误码、类名、方法名等精确词；只用关键词检索又容易错过语义相近的 SOP。因此推荐混合检索：

```text
向量召回 + BM25 关键词召回 + 分数融合 + rerank
```

当前 `app/services/knowledge.py` 已实现 BM25、本地 hybrid score、上下文扩展；`app/services/vector_store.py` 提供可选 Chroma 向量检索。向量不可用时，系统可以降级为 BM25 + 本地 rerank。

重排阶段可以使用自微调小模型，对“告警文本 + 候选文档”做相关性判断。训练数据可以来自历史告警、值班人员最终引用的 Runbook、复盘文档和人工标注。

### 13.3 Prompt 构造

告警报告 Prompt 建议包含：

- 系统角色：你是 SRE 告警研判助手。
- 告警事实：title、description、severity、alertType、labels、annotations。
- 已有证据：日志、指标、代码、Runbook、历史事件。
- 输出格式：摘要、影响范围、证据链、可能根因、建议动作、升级条件、缺失证据。
- 安全限制：不要臆造根因，不要输出敏感凭据，不要自动执行高风险操作。

生成答案时必须区分事实和推测。例如“日志中出现 connect timeout”是事实，“可能是下游依赖异常”是推测，报告里要分开写。

### 13.4 动态路由 RAG

不同告警类型选择不同知识域：

- PROBLEM：服务 Runbook、接口文档、错误码、历史故障。
- BUSINESS：指标口径、业务 SOP、数据链路、活动配置。
- EVENT：发布流程、回滚 SOP、配置变更规范。
- HOST：主机排障、容器平台、容量水位、基础设施 SOP。

这种动态路由能减少噪声，也能提高证据相关性。

## 14. 告警识别与评估

当前代码中，告警识别主要在 `app/services/alerting.py`：

- `normalize_severity()`：将 P0/P1/P2/P3 以及 critical、warning、low 等外部写法归一。
- `normalize_alert_type()`：将 problem、business、event、host 以及中文类型归一。
- `infer_alert_type()`：当外部没有提供类型时，根据标题、labels、annotations 做兜底推断。
- `incident_key()`：使用 alert_type、service、fingerprint 聚合事件。
- `assess_alert()`：生成摘要、影响、根因线索、Runbook 建议和 evidence。

评估顺序建议是：外部 priority 优先 -> 类型归一 -> 规则兜底 -> Agent 研判 -> 安全门控。P0/P1 会创建事件并触发通知，P2 创建事件但不默认通知，P3 只记录台账和趋势。

## 15. MCP 工具服务与异步工具队列

### 15.1 MCP 工具

当前 MCP 工具位于 `app/mcp_tools/server.py`：

- `evo_alert_ledger_write`：写入告警台账。
- `evo_alert_notify`：发送或记录 P0/P1 通知。

生产扩展可以继续接：日志查询、指标查询、代码搜索、发布查询、工单创建、扩缩容、回滚、PR 创建、自动部署。

### 15.2 异步工具队列

当前队列位于 `app/services/tool_queue.py`，支持：

- `LEDGER_WRITE`：所有告警写入台账。
- `INCIDENT_UPSERT`：P0/P1/P2 事件同步或记录。
- `NOTIFICATION_SEND`：P0/P1 通知。

队列支持失败重试、通知限流、死信记录和服务重启恢复。工具执行逻辑在 `app/services/tools.py`。

### 15.3 MCP、HTTP API 和 Skills 的区别

HTTP API 是系统之间的普通接口。MCP 是给 Agent 使用的工具协议，强调工具描述、参数结构和标准调用。Skills 是提示词和流程知识，不直接执行外部动作。简单说：MCP 负责“能做什么”，Skills 负责“应该怎么做”。

## 16. Skills

Skill 可以翻译成“技能文件”或“处理策略资产”。它不是几段散落在 Prompt 里的文字，而是可以扫描、校验、测试、版本化维护的项目资产。对于告警平台来说，不同类型的告警应该有不同 Skill：PROBLEM 关注日志和代码，BUSINESS 关注指标口径和数据链路，EVENT 关注变更影响，HOST 关注机器和容器状态。

当前代码已经把 Skill Registry 做成可检查入口：`app/services/skills.py` 会扫描 `skills/*/SKILL.md`，读取 front matter，校验 `name`、`description`、`workflow`、`alert_types`、目录名和安全边界，并把状态暴露到 `GET /api/agent/status` 的 `skills` 字段。

Skill 状态含义：

| 状态  | 中文解释       | 典型原因                                                 |
| ----- | -------------- | -------------------------------------------------------- |
| READY | 可用           | 字段完整，workflow 合法，安全边界存在。                  |
| WARN  | 可加载但有警告 | 缺少适用类型、示例或安全边界描述。                       |
| FAIL  | 不可用         | 缺 front matter、缺 name、缺 workflow 或 YAML 结构错误。 |

推荐的 Skill 文件结构：

```yaml
---
name: problem_log_triage
description: PROBLEM 问题类告警的日志排查流程。
alert_types:
  - PROBLEM
workflow:
  - 提取服务名、实例、接口、错误码、traceId、时间窗口
  - 查询错误日志和异常堆栈
  - 识别是否为空指针、超时、限流、依赖失败或配置错误
  - 关联 Trace 链路判断问题在本组、上游还是下游
  - 产出证据、疑似模块、下一步代码分析输入
---

这里写 Agent 的行为约束、输出格式、禁止事项和示例。
安全边界：不得泄露敏感字段，不得自动执行高风险变更。
```

按告警类型设计 Skill：

| 告警类型              | 推荐 Skill                                             | 生产排查工作流                                                                               |
| --------------------- | ------------------------------------------------------ | -------------------------------------------------------------------------------------------- |
| PROBLEM 问题类        | `problem_log_triage`、`problem_code_analysis`      | 查日志 -> 查 Trace -> 定位异常栈 -> 找代码模块 -> 查服务大盘 -> 检索 Runbook -> 生成证据链。 |
| BUSINESS 业务类       | `business_metric_triage`、`business_owner_handoff` | 确认指标口径 -> 查数据延迟 -> 看业务漏斗 -> 比对活动/配置 -> 联系业务 Owner。                |
| EVENT 事件类          | `event_change_review`                                | 关联发布/配置/扩缩容/定时任务 -> 判断是否引发异常 -> 建议回滚、暂停变更或继续观察。          |
| HOST 主机类           | `host_diagnostics`                                   | 查 CPU/内存/磁盘/网络 -> 定位异常进程/容器 -> 摘流、迁移或扩容。                             |
| SECURITY 安全类，扩展 | `security_alert_triage`                              | 查异常登录、权限变更、攻击流量、密钥风险，走安全升级流程。                                   |
| DATA 数据类，扩展     | `data_quality_triage`                                | 查 ETL、数据延迟、分区、指标口径和数据血缘。                                                 |

Skills 的生产价值是把团队经验沉淀下来。比如一次 P1 核销失败复盘后发现“先查券状态表，再查核销流水，再查幂等键冲突”更有效，就可以把这三步更新进 `coupon_verify_triage` Skill。上线前由 Registry 校验，Harness 评测确认通过，再让 Runtime 在对应告警类型中加载。

Skill 自进化也应该有人审：系统可以从复盘报告里提取候选 SOP、候选 Prompt 和候选路由规则，但不能自动把未审核的规则推到生产。更稳妥的流程是“生成候选变更 -> 人工评审 -> 小流量验证 -> 评测集回归 -> 发布”。

## 17. 评测与验收

RAG 评测指标包括 Recall@K、Precision@K、MRR、NDCG@K、HitRate。先解释这些指标：

- Recall@K：前 K 个检索结果里，命中了多少应该命中的相关文档。它关注“有没有找全”。
- Precision@K：前 K 个检索结果里，有多少是真的相关。它关注“结果干不干净”。
- MRR：Mean Reciprocal Rank，正确答案第一次出现得越靠前，分数越高。
- NDCG@K：考虑排序位置和相关性等级，越相关的文档排得越前，分数越高。
- HitRate：前 K 个结果里只要命中至少一个相关文档，就算这个 case 命中。

计算方式可以理解为：准备一批评测集，每条 case 包含告警输入、期望命中的文档或关键词。系统检索 topK，例如 topK=4，然后统计命中情况。比如 100 条告警 case 中有 96 条在前 4 个结果里命中了相关 Runbook，那么 HitRate@4 约等于 0.96。

常用计算口径：`Recall@K = 命中的相关文档数 / 该 case 应命中的相关文档总数`，`Precision@K = 命中的相关文档数 / K`，`MRR = 1 / 第一个相关文档的排名`，`HitRate@K = topK 是否至少命中一个相关文档`。NDCG@K 会把相关性等级和排序位置一起考虑，相关文档越靠前分数越高。

README 中可以使用一次本地 Harness 结果作为讲解样例：

```json
{
  "passed": true,
  "totalCases": 100,
  "topK": 4,
  "recallAtK": 0.9667,
  "precisionAtK": 0.6458,
  "ndcgAtK": 0.9053,
  "hitRate": 0.9667
}
```

这组指标的含义是：绝大多数告警都能在前 4 个结果里召回相关文档，相关文档的排序质量也保持在较高水平；Precision@K 相对低一些，说明前 4 个结果里仍有部分噪声，后续可以通过 metadata filter 和 reranker 优化。

工程 Harness 分层：

- Alert Safety Harness：验证 P0/P1 高优先级识别、报告生成、敏感信息脱敏、后台元数据不外显、工具队列入队。
- Agent Routing Harness：验证 PROBLEM/BUSINESS/EVENT/HOST 的动态路由，以及 Agent 步骤是否符合预期。
- RAG Harness：验证知识库召回指标，包括 Recall@K、Precision@K、MRR、NDCG@K、HitRate。
- API Harness：验证健康检查、认证授权、告警接入、管理员知识库接口，以及未来 SSE 流式入口。
- Tool Queue Harness：验证文档台账写入、事件同步、通知发送、依赖顺序、限流、重试和 DeadLetter 死信。

当前代码保留 `app/harness/runner.py`，可以用作工程检查入口。当前实际可运行验收命令：

```bash
python3 -m compileall app -q
python3 -m pytest -q
```

RAG 评测入口已经接入 100 条中文 case，默认数据集是 `app/rag_eval/alert_eval_cases_zh.json`。生产评测走 MySQL 中的 `KnowledgeChunk`：

```bash
python3 -m app.rag_eval.runner
```

如果本地没有启动 MySQL，只想先验证评测指标计算和 case 格式，可以使用 mock 知识库模式：

```bash
RAG_EVAL_MOCK_KNOWLEDGE=true python3 -m app.rag_eval.runner
```

评测报告会写入 `target/rag-eval-report.json`。mock 模式会根据 case 的 `expectedDocs` 生成临时知识文档，所以指标可能非常高；真实生产评测应该接入企业知识库、接口文档、SOP 和历史故障复盘后再看数值。

当前未配置 MySQL 测试库时，纯函数测试会通过，MySQL 集成测试会跳过。要跑完整集成测试，先准备测试库，然后执行：

```bash
export TEST_DATABASE_URL='mysql+pymysql://evoalert:evoalert@127.0.0.1:13306/evoharness_alert_test?charset=utf8mb4'
python3 -m pytest -q
```

建议手动验收：

```bash
docker compose up -d --build
curl http://127.0.0.1:8080/actuator/health
curl -X POST http://127.0.0.1:8080/api/alerts/webhook \
  -H 'Content-Type: application/json' \
  -d '{"source":"manual","title":"核心交易链路不可用","severity":"P0","alertType":"problem","labels":{"service":"checkout-api","instance":"pod-1"}}'
curl -u admin:admin123 http://127.0.0.1:8080/api/admin/incidents
```

当前项目已经提供一份中文 mock 评测集：`app/rag_eval/alert_eval_cases_zh.json`，包含 100 条 case，覆盖团购服务、优惠券核销、支付、订单、门店、配送、会员卡、主机资源、发布变更和数据延迟。示例 case：

```json
{
  "id": "coupon-null-pointer-001",
  "query": "P1 问题告警：团购券核销接口 5 分钟内失败率超过 20%，日志出现 NullPointerException，服务 coupon-service，实例 coupon-api-03。",
  "expectedDocs": ["团购券核销接口文档", "coupon-service 空指针排障 Runbook", "核销失败率告警 SOP"],
  "expectedRoute": "PROBLEM",
  "expectedSteps": ["查日志", "看代码", "查大盘", "检索知识库", "生成证据链报告"]
}
```

## 18. 代码解读

关键模块：

- `app/api/routes.py`：HTTP API 入口，包含告警接入、后台查询、事件确认、事件关闭、知识库接口。
- `app/services/alerting.py`：告警主链路，负责字段归一、类型识别、Incident 聚合、Trace 写入和 ToolJob 创建。
- `app/models/entities.py`：MySQL ORM 实体，包括 AlertEvent、Incident、AgentRunTrace、ToolJob、NotificationRecord、LedgerRecord、KnowledgeChunk。
- `app/services/knowledge.py`：知识库服务，负责文档 ingest、chunk、BM25、rerank 和上下文扩展。
- `app/services/vector_store.py`：可选 Chroma 向量库和 embedding 接入。
- `app/services/tool_queue.py`：异步工具队列，负责任务调度、重试、限流和死信。
- `app/services/tools.py`：具体工具执行，包括 Excel 台账和通知。
- `app/mcp_tools/server.py`：MCP 工具服务。
- `app/static/admin.html`、`app/static/admin.js`：中文后台控制台。

## 19. 高频面试回答

### 为什么做告警 Agent？

因为告警触发后，值班人员真正需要的是快速研判影响、证据、根因和下一步，而不是再手动把日志、代码、指标、知识库全部查一遍。Agent 可以把这些步骤组织成一条可追溯链路。

### 为什么用黑板式多 Agent？

告警研判涉及多个角色：定级、聚合、查日志、看代码、看大盘、查知识库、生成报告、通知和安全门控。黑板机制可以让每个 Agent 共享证据但保持职责边界，最终形成可审计的报告。

### 为什么不用一个 Agent？

一个 Agent 全做会导致上下文过大、工具权限过宽、证据混乱、难以审计。拆分后可以让每个 Agent 使用不同 prompt、不同模型、不同工具权限，风险更可控。

### 为什么不是 ReAct 主导？

ReAct 更适合单 Agent 串行工具调用。告警场景更像事件驱动系统，需要多 Agent 并行补证据、共享黑板、异步工具队列和人工审批点。

### 为什么需要 RAG？

告警文本通常不完整，很多名词、接口、错误码、SOP 和历史故障只存在企业知识库里。RAG 能把这些上下文补进报告，减少模型臆测。

### 为什么需要强证据链？

生产故障不能靠“模型感觉”。值班人员需要知道结论来自哪条告警、哪段日志、哪个指标、哪个代码位置、哪个 Runbook。证据链也是复盘和审计的基础。

### 为什么 MySQL 和 Redis 都要用？

MySQL 保存长期事实和审计数据，例如告警、事件、任务、通知、台账、Trace。Redis 保存短期上下文、热状态和 Agent 私有记忆，适合高频读写和 TTL。

### 如何做自动修复但避免风险？

自动修复必须分级。低风险动作可以自动建议或自动执行，高风险动作必须人工确认。比如扩容、重启、回滚、提交 PR、部署，都需要审批、权限控制、测试验证和回滚方案。

### 如何做 Prompt/Skill 自进化？

每次故障复盘后，把有效处置流程沉淀成 Skill 或 Runbook，由人工审核后进入知识库。系统可以统计哪些 Prompt 产生了高质量报告，逐步优化路由、模板和 rerank 数据。

### 如果接入企业内部知识库/向量库，怎么落地？

把当前 `KnowledgeService` 作为适配层，上游接内部文档、接口平台、Hive/MySQL 同步任务，下游接企业向量库。检索时使用 metadata filter 限制业务线、服务、权限和版本，再做向量召回、BM25 召回和 rerank。

---

## 20. 一条告警的完整运行时序与分支

> 先说清楚两套路径，面试和读代码都要能分得清：
> - **生产主链路**（当前 `app/services/alerting.py` + `tool_queue`）是**规则驱动**的，告警入库 → 归一化 → 建 Incident → 写 Trace → 入异步工具队列，效率高、可审计。
> - **多 Agent 运行时**（`app/agents/` 的 `event_driven_multi_agent`）是**黑板 + 认领式调度**的框架骨架，当前 `coordinator._act()` 仍是规则占位（未真正接 LLM/工具），用于沉淀"Conversational 研判入口"和事件驱动协作契约。

### 20.1 生产主链路（权威时序）

```
外部告警(Webhook/Prometheus Alertmanager)
  -> POST /api/alerts/webhook | /api/alerts/prometheus   (routes.py)
  -> AlertIngestService.ingest_webhook / ingest_prometheus (alerting.py)
      1 归一化: severity/priority -> P0..P3 (normalize_severity)
      2 识别类型: alert_type -> PROBLEM/BUSINESS/EVENT/HOST (normalize_alert_type / infer_alert_type)
      3 状态归一: status -> FIRING/RESOLVED (normalize_status)
      4 建 AlertEvent + 指纹(fingerprint, sha256) 落 MySQL
      5 按 incident_key 聚合/更新 Incident (_upsert_incident, P0/P1/P2 才建)
      6 写 AgentRunTrace (_save_trace，含脱敏后的 payload/步骤/证据)
      7 入队 ToolJob (_enqueue)：LEDGER_WRITE 必做；有 Incident 加 INCIDENT_UPSERT；P0/P1 加 NOTIFICATION_SEND
  -> ToolQueueDispatcher 轮询 (tool_queue.py)
      PENDING(run_after<=now) -> RUNNING
      按 kind 分发给 Excel/Email executor
      成功 COMPLETED；失败按 max_attempts/retry_delay 重试；超限转 DeadLetter
  -> 产出 ledger(evoharness-alert-ledger.xlsx) + 通知(log/SMTP) + 报告
```

### 20.2 告警类型 × 等级的路由矩阵

路由差异主要体现在 `handling_steps(alert_type)`（`alerting.py`）与 Incident 是否创建、是否通知：

| | P0 | P1 | P2 | P3 |
| --- | --- | --- | --- | --- |
| 建 Incident | ✅ | ✅ | ✅ | ❌ |
| 通知值班 | ✅(NOTIFY_SEVERITIES) | ✅(NOTIFY_SEVERITIES) | ❌ | ❌ |
| PROBLEM | 拉应急；Log+Trace+Code+Metrics+Knowledge | 同左，降级 | 值班窗口跟踪 | 台账 |
| BUSINESS | 应急；业务大盘+口径+漏斗+Owner | 同左 | 事件跟踪 | 台账 |
| EVENT | 应急；关联发布/变更+影响判断 | 同左 | 变更观察 | 台账 |
| HOST | 应急；CPU/内存/磁盘/网络+摘流迁移 | 同左 | 值班窗口 | 台账 |

各类型的 Agent 组合（`handling_steps`）：PROBLEM→`LogQuery/CodeAnalysis/Metrics/Knowledge`；BUSINESS→`BusinessMetric/DataQuality/Knowledge`；EVENT→`ChangeReview/Impact`；HOST→`HostDiagnostic/Metrics/Knowledge`。

### 20.3 重试与超时语义（现状）

- **工具队列**：`tool_queue_max_attempts`(3) + `tool_queue_retry_delay_seconds`(15)，`POLL` 轮询，失败按 `run_after = last_error_at + delay` 重调度，超限转 DeadLetter。
- **Agent 运行时**：`agent_runtime_max_steps`(8) 轮数预算、`max_claims_per_round`(4)/`max_claims_per_agent`(3) 限流、`final_accept_min_confidence`(0.6) 采纳门槛；预算耗尽触发 `BUDGET_EXHAUSTED` 事件。
- **缺口**：目前"重试/超时"只覆盖工具队列，**Agent 中间态不落盘**，一旦进程崩溃，这轮研判的黑板状态即丢失。

---

## 21. State / Checkpoint 设计与断点续跑

这是目前最大的设计缺口之一，也是高频面试点。目标：One Alert = 一个可持久化、可续跑的工作流实例。

### 21.1 设计目标

- 把"黑板（CollaborationBlackboard）+ 任务/证据/结论"序列化为一条**运行态记录**，做成**全息 checkpoint**。
- 任何 Agent 动作可重放、可回滚、可断点续跑到崩溃前一步。
- 幂等：同一 turn 重复执行不产生副作用的重复（工具调用按 `tool_call_id` 去重）。

### 21.2 需要落盘什么

| 字段 | 内容 | 存储 |
| --- | --- | --- |
| `turn_id / alert_id` | 工作流实例主键 | MySQL 主键 |
| `board_snapshot` | 全部 open/claimed/done 任务、artifacts、优先级 | JSON 列 |
| `events_log` | ROUN_STARTED/TASK_CREATED/TASK_CLAIMED... 顺序事件 | JSON 列（可追加） |
| `last_step` / `step_index` | 崩断时进行到哪一步 | 整数列 |
| `tool_fx_calls` | 已发起的工具调用的 id + 结果 | 表 `agent_tool_call` |
| `state` | PENDING/RUNNING/WAITING_TOOL/DONE/FAILED | 枚举列 |
| `attempt` | 断点续跑计数 | 整数列 |

### 21.3 续跑逻辑（checkpoint resume）

```
拿到一条 PENDING/RUNNING(attempt<max) 的运行记录
  -> 反序列化 board_snapshot, 重建 CollaborationBlackboard
  -> 从 last_step/step_index 继续, 跳过已完成 artifact
  -> 对"已发起的工具调用"按 id 去重, 复用其结果, 避免重复副作用
  -> 每完成一步事务性写回 board_snapshot + last_step + tool_fx_calls
  -> 达到 final_accept 或 budget 耗尽 -> 标记 DONE
```

建议落点：新增 `app/models/entities.RuntimeCheckpoint` + `app/services/checkpoint.py`，在 `EventDrivenCoordinator` 每轮首尾插入 `save()/resume()`。当前 `event_driven_runtime.run()` 只在内存里建黑板，尚未持久化——生产扩展项。

### 21.4 幂等工具调度

工具队列已具备"重试 + 死信 + run_after"，在此基础上补一层 **tool_call_id 去重**即可实现跨崩溃幂等：LEDGER_WRITE/NOTIFICATION_SEND 以 (alert_id, kind, fingerprint) 作为天然去重键。

---

## 22. 大上下文模型：Kimi 百万级 Token 怎么用

你问得对：现在上下文管理很"抠门"。原因不是模型不支持，而是**默认值是为小窗口模型（如 7B/qwen、gpt-4o-mini）调优的**，防止上下文爆长导致费用高、速度慢、检索稀释。看几个关键配置：

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `redis_memory_max_messages` | 40 | 短期记忆只留最近 40 条 |
| `memory.py` 压缩常量 | 500 字符摘要 | `compact_history_for_prompt`/`summarize_history_for_memory` 把历史压得很短 |
| `knowledge_top_k` / `candidate_k` | 4 / 16 | RAG 只回 4 段，防止塞爆小窗口 |
| `agent_runtime_max_steps` | 8 | Agent 轮数预算 |

### 22.1 Kimi(Moonshot) 接入

Kimi 提供 OpenAI 兼容 API，`AiClient` 已有 `openai` provider，无需改代码，改 `.env`：

```
AI_PROVIDER=openai
OPENAI_BASE_URL=https://api.moonshot.cn/v1
OPENAI_API_KEY=sk-...
OPENAI_MODEL=kimi-k2-0905-preview
```

### 22.2 大窗口下怎么放开

我新增了配置（`config.py`）作为"放大开关"，一键从"小模型省流模式"切到"Kimi 大窗口模式"：

| 配置 | 默认 | 大窗口建议 |
| --- | --- | --- |
| `provider_context_window_tokens` | 2 亿 | 与 Kimi 窗口对齐 |
| `memory_use_full_context` | false | true：不再强制压缩近期消息 |
| `memory_max_messages_full_context` | 4000 | 把"只留 40 条"放大到完整会话 |
| `knowledge_top_k_full_context` | 24 | RAG 召回候选翻数倍 |

这样做的收益：保留完整对话/日志上下文、召回更多证据片段，模型"读得多所以判断更准"；代价是每次请求更大、更慢、更贵，所以要配合第 24 节的耗时估算做 ROI 权衡。

> 面试回答模板：**"记忆/上下文长度不是越大越好，而是受模型窗口、成本、延迟和检索稀释四者约束。小窗口模型走压缩+topK=4；接入 Kimi 这类百万级窗口后，我们把记忆限量从 40 条放大到数千条、topK 从 4 放到 24，同时用 checkpoint 保证长流程可续跑。"**

---

## 23. 代码分析沙箱（轻量化本地 + clone）

一条告警往往跨多个微服务，值排 Agent 需要**克隆对应服务代码并静态分析**。生产上为了隔离和安全要走沙箱；本地希望轻量起。落了一个模块：`app/sandbox/sandbox.py`。

### 23.1 双后端

- `sandbox_backend=process`（默认，零依赖）：subprocess + `resource.setrlimit` 限制内存/CPU/超时，适合本地演示与开发。
- `sandbox_backend=docker`：走 `docker CLI` 起一次性容器，`--memory/--cpus/--network=none` + 目录只读挂载，隔离更强，适合 CI/生产。

### 23.2 用法

```python
from app.sandbox import create_sandbox
sb = create_sandbox()
repo = sb.clone_repo("https://github.com/org/coupon-service.git", branch="main")  # host 侧 clone
result = sb.execute(["bash", "-c",
    "grep -rn 'NullPointerException\\|close()' --include=*.java src | head -n 50"],
    cwd=repo)
# SandboxResult(exit_code, stdout, stderr, duration_ms, outcome: succeeded/timeout/error)
```

### 23.3 与 Agent 的衔接（设计）

`handling_steps` 里处于 `待接入工具` 的 `LogQueryAgent/CodeAnalysisAgent` 就是沙箱的消费方；`mcp_tools/server.py` 可加一个 `code_analyze` 工具：入参 = repo/branch/关键字/时间窗口，出参 = `SandboxResult`，作为证据写入 Trace。

### 23.4 安全考虑

- 默认 `--network=none`，沙箱内无网络，避免代码被外带。
- 只读挂载源码，产物(报告/补丁)从 stdout 回传，不写宿主。
- `find/head` 限制文件数和输出 20KB，防止回显炸弹。

---

## 24. 耗时估算与多服务并行分析

"分析一轮告警大概要多久？能并行吗？" 这是实打实的容量/SLO 问题。落了一个模块：`app/services/estimation.py`。

### 24.1 串行 + 并行模型

`总耗时 = Σ(串行阶段) + Max(并行波次)`。这里的关键可并行点是：**多个微服务被同一告警波及，各自的沙箱代码分析可以并行**，用线程池按 `estimation_max_parallel`(5) 分成若干波次（wave），把 `N×T_tool` 压成 `ceil(N/parallel)×T_tool`。

```python
from app.services.estimation import estimate_alert_assessment_time, format_estimate
from app.core.enums import Severity, AlertType
print(format_estimate(estimate_alert_assessment_time(
    Severity.P1, AlertType.PROBLEM,
    ["coupon-service", "payment-service", "order-service", "deal-service", "biz-platform"])))
```

### 24.2 分阶段延迟常量（可被观测回写校准）

| 阶段 | 默认 | 含义 |
| --- | --- | --- |
| `ESTIMATION_DEFAULT_LLM_MS` | 3000 | 单次大模型调用（含思考/输出） |
| `ESTIMATION_DEFAULT_RAG_MS` | 500 | 一次 embedding+BM25+rerank 检索 |
| `ESTIMATION_DEFAULT_TOOL_MS` | 8000 | 一次沙箱启动+代码分析 |
| `ESTIMATION_MAX_PARALLEL` | 5 | 并行波次宽度 |

按默认值，一条 P1/PROBLEM 波及 5 个服务的单轮预估约 27s（串行 ~18.8s + 并行沙箱 1 波 8s）。若这 5 个服务串行分析则要 18.8 + 40 = 58.8s，并行把整体耗时压了一半以上。

> 落地建议：在 `AlertIngestService` 入库时调用 `estimate_alert_assessment_time` 记录预估，真实遥测（LLM/RAG/沙箱耗时）回写后做回归校准，形成闭环 SLO。

---

## 25. 参考项目与可迁移资产

本项目的 `event_driven_multi_agent` 骨架与参考项目 `mindbridge-py`（同样是事件驱动黑板多 Agent runtime）同源。可直接迁移/对齐的资产：

| mindbridge-py 组件 | 本项目的对应 | 迁移价值 |
| --- | --- | --- |
| `EventDrivenAgentRuntimeService.run()` 主运行时 | `app/agents/event_driven_runtime.py` | 已是同结构，可对其补 checkpoint |
| `EventDrivenCoordinator` 主循环 | `app/agents/coordinator.py` | 已对齐（derive_missing_work / claim / accept_final） |
| `RedisShortTermMemoryStore` + `compact_history_for_prompt` | `app/services/memory.py` | 已对齐；大模型模式下可绕过压缩 |
| `MindBridgeAgentHarness.run()` 编排外壳 | `app/agents/harness.py` | 本项目 harness 是占位，可参考其 session→runtime→保存的编排 |

真正值得动手的迁移/增强优先级：
1. **补 checkpoint + 断点续跑**（第 21 节）——面试必问，也是可靠性短板。
2. **Agent 运行时真正接入 LLM/RAG/沙箱**——目前 `_act()` 是规则占位，把它接到 `AiClient` + `KnowledgeService` + `CodeSandbox`。
3. **Kimi 大窗口模式一键切换**（第 22 节）——配置已就位，补记忆检索侧的开关逻辑。
