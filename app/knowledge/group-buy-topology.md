# 团购验券业务拓扑（核心故事场景）

本项目的所有告警研判、示例数据集和评测用例都基于同一条业务故事：**到店团购**。讲清楚这条链路，才能讲清楚"一条告警为什么会从某个服务冒出来、会传播到哪里"。

## 1. 业务主链路

```text
用户浏览套餐 -> 下单 -> 支付 -> 发券 -> 到店验券核销
(deal-service) (order-service) (payment-service) (coupon-service) (coupon-service + merchant-service)
```

1. **浏览/选套餐**：用户在 App 打开团购频道，deal-service 返回套餐列表与详情（库存、有效期、适用门店）。
2. **下单**：order-service 创建订单，锁定套餐库存，生成待支付订单。
3. **支付**：payment-service 调用第三方支付渠道（微信/支付宝），回调后推进订单状态。
4. **发券**：支付成功事件经 MQ 异步通知 coupon-service，券入账到用户账户。
5. **验券核销**：用户到店，商家扫码，coupon-service 的 `/coupon/verify` 接口校验券状态，调用 merchant-service 确认门店营业/归属，核销入账并记账（资损口径）。

## 2. 微服务清单

| 服务 | 职责 | 关键接口 | 核心依赖 |
| --- | --- | --- | --- |
| deal-service | 套餐/商品查询、库存 | `/deal/query` `/deal/detail` | MySQL(deal 库)、Redis(商品缓存) |
| order-service | 订单创建、状态机 | `/order/create` `/order/query` | MySQL(order 分库)、MQ、deal-service |
| payment-service | 收单、渠道对接、回调 | `/pay/submit` `/pay/callback` | 第三方渠道、MQ、order-service |
| coupon-service | 发券、验券核销 | `/coupon/grant` `/coupon/verify` | MySQL(coupon 库)、Redis(券状态)、merchant-service |
| merchant-service | 门店/商户档案、核销入账 | `/merchant/query` `/merchant/redeem` | MySQL(merchant 库) |
| api-gateway | 统一接入、鉴权、限流 | 全部入口流量 | 下游全部服务 |

## 3. 常见故障传播路径（讲排障故事的核心）

- **coupon-service 挂 → 验券不可用**：商家无法核销，用户被拒，现场客诉激增；属于 P0/P1，直接影响交易闭环最后一公里。
- **payment-service 回调延迟 → 有单无券**：订单已支付但券未入账，用户投诉"付了钱没有券"；表象在券侧，根因常在支付回调/MQ 积压。
- **deal-service 缓存击穿 → 下单量下跌**：套餐详情超时，转化率下跌；业务指标告警（BUSINESS）先行，主机/服务告警随后。
- **merchant-service 慢 → 验券 RT 升高**：coupon-service 同步调用 merchant-service，下游慢会拖垮验券接口，甚至打满连接池（故障传播的典型例子）。
- **api-gateway 限流误伤 → 全站入口错误率升高**：所有服务同时告警时优先怀疑入口层，而不是逐个排查下游。

## 4. 排查时的时间窗口约定

- 一切日志/指标/Trace 查询以告警 `startsAt` 为锚点，默认窗口 ±10 分钟。
- 发布类(EVENT)告警要把窗口对齐到发布动作前后 15 分钟。
- 跨服务联查一律带 `traceId`（如 `trace-2026090198`），没有 traceId 再退化为 service+时间窗口。

## 5. 面试一句话

> "我们的示例场景是到店团购：套餐、订单、支付、发券、验券核销五个微服务加网关。告警研判的价值在于：验券失败的告警，机器人能沿 coupon-service → merchant-service → 网关 这条拓扑，自动把日志、Trace、代码和 Runbook 组织成证据链，而不是让值班人员挨个服务去翻。"
