# 本机真实联调记录

## 模型配置更新

最新复测已成功：使用项目 `.env` 中的平台地址和用户密钥，`gpt-5.5` 的 Responses 流式接口返回实际中文文本。非流式接口曾返回 HTTP 200 但 `output=[]`，因此本机启用 `OPENAI_RESPONSES_STREAM=true`，后端收齐完整文本再解析；空结果或中断不算成功。

8092 已重启完成切换。真实 Webhook 验证产生告警 5、事件 4，MySQL Trace 中 `modelStatus=SUCCESS`、`model=gpt-5.5`，摘要为“待验证告警：合成支付服务错误率从1%升至8%，当前仅有告警描述，根因尚未确认。”本轮回归 **226 passed**。下面的 404 记录属于此前失败状态，不再代表最新可用性。

2026-09-26：已新增 Responses 适配（`OPENAI_WIRE_API=responses`，`store=false`），Webhook 可调用模型生成摘要，并在 Trace 的 `assessment.modelStatus` 标记 `SUCCESS` / `FALLBACK` / `RULES`。失败保留规则结果，不改变优先级，不执行自动修复。模型请求是同步的，可能增加接入延迟，尚非生产级异步研判。

本机 `.env` 选择 `aiaaa.cc` 的 `gpt-5.5`，但使用用户提供的项目密钥实测返回 `404 model_not_found`，提示该账号分组不提供该模型，因此**GPT 尚不能通过该账号使用**。早先连接测试受系统注入的模型环境变量影响，不能作为此账号成功依据。本地启动脚本现已明确用项目 `.env` 覆盖继承的模型配置，避免混用账号。不要将历史“连接成功”理解成此账号已获 GPT 权限。

自动化测试请显式设置 `AI_PROVIDER=mock`，避免 `.env` 开启真实模型后回归测试产生费用。

日期：2026-09-26。此记录补充并取代此前“仅模拟前端、数据库测试跳过”的本机状态说明，不代表生产验收。

## 实际运行环境

通过 Homebrew 安装原生 MySQL 8.4.11、Redis 8.10.2，无 Docker。此版本组合与 Compose 的 MySQL 8.0 / Redis 7.2 不同，Compose 仍未验收。

| 服务 | 地址 | 说明 |
| --- | --- | --- |
| 告警前后端 | http://127.0.0.1:8092 | FastAPI 提供前端及真实 API |
| MySQL | 127.0.0.1:13306 | 演示库 `evoharness_alert`，专用测试库 `evoharness_alert_test` |
| Redis | 127.0.0.1:16379 | 应用使用 DB 0，独立记忆测试使用 DB 1 |
| 原静态预览 | http://127.0.0.1:8091 | 只有模拟前端，不是 API 服务 |

进入 8092，点击“连接后端”，本机演示账号 `admin/admin123`。默认仍显示模拟模式，连接后才显示 MySQL 数据。Basic 认证与默认账号仅供本机开发，不能直接对外部署。

所有服务仅绑定回环地址。项目数据和日志位于 `target/local-stack/`，不纳入 Git 或源码包。MySQL 使用独立数据目录，root 随机密码只存于本地权限为 600 的文件。未注册开机自启动；重启电脑后需重新启动。

## 复现

```bash
/opt/homebrew/bin/brew install mysql@8.4 redis
python3 scripts/local_stack.py
python3 scripts/check_local_integration.py
TEST_DATABASE_URL='mysql+pymysql://evoalert:evoalert@127.0.0.1:13306/evoharness_alert_test?charset=utf8mb4' REDIS_URL='redis://127.0.0.1:16379/1' python3 -m pytest -q
```

脚本目前面向 Apple Silicon Homebrew 路径，使用当前 Python 环境中的项目依赖。测试库必须可清空，禁止将 `TEST_DATABASE_URL` 指向演示库或业务库。冒烟脚本每次会保留两条明确标记的中文测试告警及其已关闭事件，Redis 临时测试 key 在完成后删除。

## 验收结果

- pytest：**218 passed，0 skipped**。此前三项 MySQL 测试已实际执行；仍有四条 FastAPI 生命周期弃用警告。
- 真实 HTTP：健康检查、账号认证、两条 P0 Webhook 告警入库、相同指纹聚合到同一个事件。
- 真实后台工作线程：六个队列任务全部 SUCCESS，包含台账、事件记录及通知任务。
- MySQL 查询：能够读取告警、Agent 轨迹、台账和通知记录；接手、备注、关闭实际落库。
- Redis：PING、45 条写入裁剪到 40 条、86400 秒 TTL、手机号脱敏、读取及整体回填均通过。测试只核验内存存储组件，不声称已覆盖完整多轮聊天的缓存失效恢复。
- 浏览器：未拦截 API，真实登录 8092 并显示 MySQL 中两条测试告警。

机器结果：`target/local-stack/integration-result.json`；浏览器截图：`target/local-stack/live-console.png`；API 日志：`target/local-stack/api.log`。

**边界**：AI 仍是 mock，通知仍是 log，不发送真实邮件；Excel 台账为真实本地文件。未验收外部大模型、SMTP、生产日志/代码取证、群机器人、80 人并发、故障重启和生产部署。不能将本次通过理解为生产全链路已完成。
