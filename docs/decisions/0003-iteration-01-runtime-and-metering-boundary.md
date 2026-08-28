# ADR-0003: Iteration #1 runtime policy and metering boundary

| Field | Value |
| --- | --- |
| Status | Accepted — Product Owner decision, 2026-08-28 |
| Scope | Strategic runtime policy and Iteration #1 observation boundary |
| Related requirements | `FR-16`、`FR-17`、`NFR-03` |

## Decision

- `Xray` 是 SparkLink-wide production primary。
- `sing-box` 是 SparkLink-wide DR / technology hedge；具体 sing-box protocol baseline 可以演进。
- strategic role / desired policy 与 live observed runtime state 必须严格区分。该 policy 不修改或覆盖 `docs/runtime/2026-08-24_THREE_VPS_LIVE_RUNTIME_BASELINE.md` 中的 observed facts。
- Iteration #1 的 primary metering observation surface 是 Xray production paths。
- sing-box accounting 保留为 extension boundary。如果后续 evidence 证明某 sing-box path 当前承载 production user traffic，必须显式记录 coverage gap 并作出纳入计量的 decision，不得静默忽略。
- Metering 是旁路的 observational capability，不成为 production proxy data path 的 inline dependency。Metering failure 不得导致 data plane failure。

## Consequences

当前 Xray Stats API 的可用性、DediRock 的 statistics gap 以及 sing-box/AnyTLS 的 observed state 仍以 2026-08-24 runtime baseline 为准。`Xray = primary` 不等于所有 Xray paths 已具备完整 metering coverage；primary surface 仍需按 runtime capability 显式标记 gap。

本 ADR 不选择 Collector、database、API、runtime hook、technology stack 或具体 failure-recovery implementation。

## Source references

战略 runtime policy 与 Deployer profile context 参考 `sparklink-deployer` 当前提交的 [ADR-0001](https://github.com/enderealP1g/sparklink-deployer/blob/d6b9f0dc626aa5ea67cf96c24ec96ee2e06cb3ee/docs/adr/0001-xray-primary-singbox-dr.md)。Metering boundary 与 observed-state precedence 受 SparkLink 的 [Requirements](../requirements/ITERATION_01_USAGE_METERING.md)、[As-Is Context](../architecture/ITERATION_01_AS_IS_CONTEXT.md) 和 [2026-08-24 runtime baseline](../runtime/2026-08-24_THREE_VPS_LIVE_RUNTIME_BASELINE.md) 约束。
