# 智能告警协同助手：源码交付说明

2026-09-26 更新：本机 MySQL、Redis 与 FastAPI 已启动并完成真实联调，pytest **218 passed、0 skipped**。可访问 `http://127.0.0.1:8092`。AI 仍为 mock，通知为 log；详细范围见 [本机真实联调记录](docs/local-integration.md)。以下 2026-09-25 的结果保留为历史记录。

本包是 EvoHarnessAlert 当前工作目录的开发快照，包含尚未提交的项目代码。适合阅读、二次开发和本地验证；并非飞书生产化规格的完整验收版本。请先看 [文档与代码对照表](docs/spec-code-map.md)，再阅读长篇 [README](README.md)。

新增交付：[专业运维控制台与 100 条回放样本](docs/frontend-dataset.md)、[项目背景 / 技术栈 / 50 条简历亮点](docs/resume-bullets.md)。控制台包含分栏表格、五类详情标签、独立数据集、模拟/后端模式及本地 Lucide 图标。Playwright 30 组检查通过，覆盖 1440/1920/768/390px 布局与拦截 API 的成功/失败路径；真实数据库联调另行验收。回放样本是合成数据，不是生产金标。

## 包含内容

| 目录 | 中文说明 |
| --- | --- |
| `app/api`、`app/static` | FastAPI 接口和原生前端 |
| `app/agents` | 当前黑板协作、协调器、对话业务编排 |
| `app/services` | 告警接入、知识检索、记忆、工具队列、通知等服务 |
| `app/models` | MySQL 业务实体 |
| `skills` | 当前两份通用技能文件，尚未完成文档中的四类独立技能包 |
| `app/knowledge` | 示例排障手册（Runbook） |
| `app/rag_eval` | 中文检索评测样例与运行器 |
| `tests` | 当前自动化测试 |
| `examples` | 与当前 Webhook 字段一致的 P0 中文输入 |
| `MANIFEST.json` | 包内文件清单、大小和 SHA-256 校验值（打包时生成） |

打包采用文件白名单，不包含 `.env`、Git 历史、缓存、模型权重、克隆仓库、实际运行台账及历史测试产物。不包含与项目无关的线程练习文件。

## 本地启动

推荐 Python 3.11。Docker Compose 定义了 MySQL、Redis 和应用，默认 AI 为 mock，邮件为 log 模式；启动会下载镜像和依赖。

```bash
docker compose up -d --build
curl http://localhost:8080/actuator/health
curl -X POST http://localhost:8080/api/alerts/webhook \
  -H 'Content-Type: application/json' \
  --data-binary @examples/problem-p0.json
curl -u admin:admin123 http://localhost:8080/api/admin/incidents
```

浏览器访问 `http://localhost:8080`。初始化演示账号为 `admin/admin123`；当前 `viewer/viewer123` 也被赋予管理员角色，不能当作生产只读角色。默认密码、Basic 认证和当前权限实现仅适合受控开发环境，外部部署前需要完成身份与租户权限改造。

本次整理未运行 Docker Compose，以上是按现有配置给出的启动步骤，不代表容器集成验收通过。

## 数据集与台账

- `app/rag_eval/alert_eval_cases_zh.json`：100 条中文评测样例。不能声称是已经经过真实生产验证的金标数据；尚需冻结工具响应、真实文档/切片标识与人工标注。
- `data/alert-sample-dataset.json`：演示告警样例，包含在源码包中。
- `data/evoharness-alert-ledger.xlsx`：运行生成的告警台账，记录处置过程；不是评测集，不随源码分发。
- Mock 知识库模式的检索指标不能用作生产 RAG 效果证明。

## 本次验证

2026-09-25，本机 Python 3.9：补充功能点及回放数据测试后，`python3 -m pytest -q` 得到 **215 passed、3 skipped**。Playwright 前端交互检查通过，已检查桌面、390px 手机和 768px 平板布局。详见 [功能点与测试对照](docs/testing.md)。跳过的 3 项是未设置 `TEST_DATABASE_URL` 的 MySQL 集成测试。存在 FastAPI `on_event` 弃用警告。容器、SMTP、外部模型、生产数据源和并发容量未验收。

```bash
python3 -m compileall app scripts -q
python3 -m pytest -q
```

完整数据库测试需指向专用、可清空的 MySQL 测试库：测试 fixture 会删除并重建表，禁止连接业务库。

```bash
TEST_DATABASE_URL='mysql+pymysql://evoalert:evoalert@127.0.0.1:13306/evoharness_alert_test?charset=utf8mb4' python3 -m pytest -q
```

现有 `app/harness/runner.py` 仍创建 SQLite 临时库，因此本次没有将其作为 MySQL 工程验收结果。此项是对照表中的明确待改造内容。

## 再次生成交付包

在项目任意目录执行 `bash scripts/package-release.sh`（路径按实际位置调整）。脚本会生成 `target/evoharness-alert.zip` 和 `target/evoharness-alert.tar.gz`，并回读校验全部文件。产物是本地交付包，尚未上传或发布下载地址。
