# coupon-service 验券核销 Runbook

## 适用告警

- `/coupon/verify` 5xx 错误率升高、验券失败率上升（PROBLEM/BUSINESS）
- 发券延迟、有单无券（与 payment-service/MQ 联查）
- coupon 库连接池耗尽、Redis 券状态缓存异常

## 业务上下文

验券核销是团购闭环的最后一公里：商家扫码 → coupon-service 校验券状态（未使用/未过期）→ 调 merchant-service 确认门店 → 核销入账。核销记录是资损与结算口径的事实源。验券失败直接造成门店现场客诉，P0/P1 级别优先处理。

## 排查步骤

1. **确认影响面**：按城市/门店维度聚合失败率，判断是个别门店还是全量；`labels.city`、`labels.endpoint=/coupon/verify`。
2. **查错误日志**：以 traceId 检索核销接口错误堆栈，常见关键字：`NullPointerException`、`RedisTimeoutException`、`Connection is not available`。
3. **查下游 merchant-service**：coupon-service 同步依赖门店校验，下游 RT 升高会拖垮本服务；确认 merchant-service 的 P99 与错误率。
4. **查券状态缓存**：Redis 超时或缓存击穿会导致校验失败；确认缓存命中率、大 key、慢命令。
5. **查数据库**：coupon 库慢 SQL、锁等待（核销事务热点集中在热门券模板）。
6. **查发布记录**：窗口内有发布/配置变更则优先关联（EVENT 路由）。

## 止血动作

- 个别实例异常：摘除异常实例流量。
- 依赖门店校验故障：开启"门店校验降级开关"（跳过实时校验，异步对账补校验）——需业务 owner 确认资损风险。
- 缓存故障：回源降级 + 限流保护数据库。
- 数据库连接池满：临时扩容连接 + 限流非核心查询。

## 升级条件

- 全量城市验券不可用超过 5 分钟 → 立即升级 P0，拉应急。
- 核销数据疑似错账（重复核销/资损）→ 立即冻结核销并升级到结算/财务值班。

## 常见根因 Top

1. merchant-service 抖动拖垮同步调用链
2. Redis 券状态缓存超时/击穿
3. 核销热点行锁等待
4. 最近发布引入 NPE
5. MQ 发券消息积压导致的"有单无券"误报
