# ADR-0006: Production identity and customer cycle reconciliation

| Field | Value |
| --- | --- |
| Status | Accepted — Product Owner decision, 2026-08-29 |
| Scope | Production MVP 的 stable `User` identity、Customer Billing Cycle 与 Provider Resource Cycle 边界 |
| Related decisions | [`ADR-0001`](0001-project-domain-identity-and-serving-relationships.md)、[`ADR-0004`](0004-production-mvp-vertical-slice.md)、[`ADR-0005`](0005-metering-hardening-and-automatic-collection.md) |

## Decision

- SparkLink 使用自己的 stable `User` identity。runtime UUID、protocol credential 和其他 technical identity 只作为 `Credential`，通过 `Credential → User` mapping 参与 Usage attribution；`Subscription` 不是 attribution authority。
- `Portal access token` 与 `Subscription token` 是不同的 delivery/authentication material。`Subscription` 继续是由当前 User、Entitlement、Pool/Node membership 和 Credential mapping 生成的 projection。
- `Customer Billing Cycle` 采用 `Asia/Shanghai` timezone，以每月 15 日 00:00 至次月 15 日 00:00 为当前 policy；`2026-09-15 00:00 Asia/Shanghai` 是本次 Production MVP 的 baseline。baseline 之前的 Usage 保留为 `legacy/pre-baseline`，不得删除、清零或重写。
- `Provider Resource Cycle` 使用各 Infrastructure Resource 已验证的 instance local timezone 和独立 provider evidence。它与 `Customer Billing Cycle` 分离；contract `Next Due` 或地理位置不能单独证明 provider traffic reset。
- Plan、Entitlement、Allowance 或 Credential mapping 的后续变化只影响后续 projection/attribution；已经落账的 Usage 保持 append-preserving history。

## Consequences

Customer view 以 `Customer Billing Cycle` 聚合，Infrastructure view 可以保留独立的 provider-cycle metadata。任何无法确认的 provider reset、allowance、upgrade pricing/proration 或 attribution coverage 必须呈现为 `Unknown`/manual review，不得用默认值伪造完整性。

## Non-Decisions

本 ADR 不决定 numeric allowance、upgrade/proration 规则、provider cycle acquisition、DediRock remediation、AnyTLS production promotion、hard quota enforcement 或具体长期 storage/technology stack。

## Source references

- [Iteration #1 Requirements](../requirements/ITERATION_01_USAGE_METERING.md)
- [Production MVP reconciliation](../requirements/ITERATION_01_PRODUCTION_MVP_RECONCILIATION.md)
- [2026-08-29 identity/cycle operations record](../operations/2026-08-29_PRODUCTION_IDENTITY_CYCLE_RECONCILIATION.md)
