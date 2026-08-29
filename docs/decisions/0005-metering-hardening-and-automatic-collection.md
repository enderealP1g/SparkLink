# ADR-0005: Metering hardening and automatic collection

| Field | Value |
| --- | --- |
| Status | Accepted — Production Hardening, 2026-08-29 |
| Scope | Xray observation lifecycle、Usage ledger correctness 与 automatic collector |
| Related decisions | [`ADR-0003`](0003-iteration-01-runtime-and-metering-boundary.md)、[`ADR-0004`](0004-production-mvp-vertical-slice.md) |

## Context

Production MVP 已有 Xray read-only Stats API 与 append-preserving Usage ledger，但原本以 Windows manual collection 为正常操作路径。本轮 hardening 需要处理 counter reset、process restart、duplicate/out-of-order observation、partial Node failure 和 stale coverage，同时保持 `proxy data plane` 独立可用。

2026-08-29 的 isolated AnyTLS investigation 证明 custom `sing-box` build 可以观察 `users[].name`，但 installed binary 不含所需 API，且 reset/restart loss 与 durable `Credential → User` mapping 仍未解决。因此本 ADR 不改变 AnyTLS 的 `Deferred pending reliable metering` 状态。

## Decision

- `Xray Stats API` 是当前正式 metering observation surface；collector 以 read-only SSH pull 读取 per-user counters，并只向 Control Plane 发送 hashed runtime identity、counter、`counter_epoch` 与 observation time。
- Automatic collector 运行在已有 protected Windows control plane，使用现有 SSH aliases/keys；QQG 不接收跨 Node collection key。Python interval loop 是 collector service，Windows `Task Scheduler` 只作为 OS process supervisor，不承载产品 scheduling 或业务规则。
- Windows admin secret 以 `LocalMachine` DPAPI 加密文件保存，并由 ACL 限定为当前 operations account 可读；secret 明文只在 collector process memory 中短暂存在。
- One-shot collector 保留为 manual operations fallback。单个 Node 的 SSH、Stats API 或 ingest failure 只写入 `coverage gap` 并继续其他 Node；不将 failure、empty source 或 unobservable bytes 记为 zero。
- `counter_epoch` 由 Node boot/process context 组成。same-epoch counter decrease 只生成 reset/non-monotonic ledger record，不产生负数；process epoch 变化不会删除历史 ledger。重复 observation 必须 idempotent；observation conflict 返回 conflict；late observation 不重新计算既有后续 delta。
- `coverage` 按 configured freshness window（默认 900 秒）解释；过期的 `available` 在 User/Admin view 中呈现为 `stale`/`unknown`，而不是继续宣称当前可用。
- Collector、Control Plane 和 coverage state 都位于 management/metering plane，不能成为 Xray、Nginx、WireProxy 或其他 proxy forwarding path 的 inline dependency。

## Consequences

- 发生 restart、API reset、remote failure 或 collection gap 时，Customer Usage 可能明确显示 `Unknown`/coverage state；系统不伪造完整性，也不清零历史 Usage。
- 当前自动路径仍依赖 Windows host 登录后的 OS supervisor 和 protected admin secret；若该 host 不可用，one-shot collector 是回退方式。
- x-ui persistent counters、DediRock source gap、future `sing-box`/provider adapters 仍需要独立 provenance/reconciliation decision，不自动混入 Xray Customer Usage。

## Not in this decision

本 ADR 不引入 hard quota enforcement、billing/proration、Portal feature、product scheduler、fleet orchestration、AnyTLS production promotion 或新的 technology stack。
