# Iteration #1 Production MVP reconciliation

| Field | Value |
| --- | --- |
| Status | Accepted implementation-scope reconciliation — Product Owner authorization, 2026-08-29 |
| Canonical baseline | [`ITERATION_01_USAGE_METERING.md`](ITERATION_01_USAGE_METERING.md) |
| Implementation decision | [`ADR-0004`](../decisions/0004-production-mvp-vertical-slice.md) |

## Purpose

本文件只记录 Product Owner 对当前 Production MVP 的 scope reconciliation。它不重写已 baseline 的 `FR`、`NFR`、`AC` 或 `Open Question` semantics。

## Reconciled implementation scope

以下项目原先在 Requirements 的 `Out of Scope` 中被保留为后续 implementation area；本次 Product Owner override 允许其进入当前最小 production vertical slice：

- stable `User` identity、`Credential → User` mapping 与最小 User administration；
- `Free` / `Basic` / `Plus` Plan、Entitlement view 与 upgrade-only manual fallback；
- `STANDARD` / `PREMIUM` Resource Pool、Node membership、`hypro02` 的 `PREMIUM / CONDITIONAL` operational record；
- append-preserving SQLite Usage store、Xray read-only Stats API collector/ingest、Customer Usage 与 Infrastructure Usage views；
- 最小 customer Portal 与 Admin observability；
- V2rayN/V2rayNG compatible Subscription projection，以及既有 `sub.enrpiglink.top` delivery boundary 的 discovery/reuse。

## Preserved constraints

- `FR-09`–`FR-17`、`NFR-01`–`NFR-06` 的 attribution、历史完整性、cycle separation、unknown/gap 与 data-plane isolation semantics 保持不变。
- 不启用 hard quota enforcement；Allowance 只作为 view/policy input。
- AnyTLS 在 reliable per-user accounting 前不进入正式 user-facing Subscription。
- 不把 `UUID`、credential、subscription token、private key、password 或其他 private runtime material 写入 Git。
- 不修改 `sparklink-deployer` 职责；不把 Portal、Billing 或 Metering 业务逻辑放入 Deployer。
- 未裁决的 proration、upgrade allowance、provider cycle acquisition、DediRock Stats API remediation 和 AnyTLS accounting 仍保持 Open/Deferred。
