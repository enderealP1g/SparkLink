# Iteration #1 Production MVP Architecture

| Field | Value |
| --- | --- |
| Status | To-Be implementation baseline — 2026-08-29 |
| Decision | [`ADR-0004`](../decisions/0004-production-mvp-vertical-slice.md)、[`ADR-0005`](../decisions/0005-metering-hardening-and-automatic-collection.md) |
| Scope | User Identity + Usage Metering + minimum Portal/Subscription vertical slice |

## Boundary

本架构只覆盖当前 MVP 的最小闭环，不是完整 Control Plane，也不替代 `sparklink-deployer` 的 Node Factory 职责。

```text
Existing Xray Nodes                 SparkLink Control Plane
-------------------                ------------------------
Xray Stats API ────── read-only ──→ raw observations
                                         ↓
                              Credential → User mapping
                              Node / Pool effective history
                                         ↓
                              append-preserving Usage ledger
                                  ↙                    ↘
                         Customer Usage          Infrastructure Usage
                                  ↓                    ↓
                              Portal / Admin views
                                         ↓
                              Subscription projection
```

`sing-box` / AnyTLS 不在第一版正式 observation surface 内；其 extension boundary 保留，但没有可靠 per-user accounting 时不能发行给 production users。

## Durable model

SQLite 中的核心关系为：

```text
users
  └─< entitlements >─ plan
  └─< credentials >─ node
  └─< subscriptions

resource_pools
  └─< node_pool_memberships >─ nodes ── infrastructure_resources (optional reference)

usage_observations (raw, append-preserving)
  └─< usage_ledger (idempotent derived deltas) >─ users / nodes / pools / billing cycles
```

`usage_observations` 保留 source、Node、counter epoch、sample time、raw counters、mapping status 和 provenance。`usage_ledger` 只接受一次同一 source observation/delta。当前 Plan/Entitlement/Allowance 不回写历史 ledger。

## Runtime observation

- RackNerd、VMISS：读取 verified loopback Xray Stats API；`x-ui client_traffics` 只作为 recovery/reconciliation source，不与 Stats API counters 直接相加。
- `hypro02`：当前已验证 loopback-only Xray Stats API；Stats API failure 不能影响 Xray serving。post-identity 当前没有 counter rows 时显示 coverage `Unknown`，不补成 zero。
- DediRock：继续记录 per-user statistics coverage gap；不使用整机或 Nginx totals 伪造 User Usage。
- Windows automatic `collector service` 通过 SSH read-only query 获取 counters，哈希 runtime identity 后向 Control Plane ingest；one-shot `manual collector` 保留为 fallback。SSH private keys 只留在 Windows protected runtime location。
- AnyTLS、provider resource cycle 和 future adapters 通过独立 capability/status 表示，不自动获得 Customer Usage authority。

## User and Credential migration

迁移过程分为两步：

1. 从各 Node read-only 读取技术身份，建立 `Credential` record 和 `runtime_ref_hash`；不把 UUID、password 或完整 URI 写入 repository。
2. 由 Admin 明确确认现实 User mapping、Plan、Entitlement 和 billing cycle；无法确认的 credential 保持 `Unresolved / Needs Mapping`，不会被自动分配到 User。

当前已完成已知 production User 的 stable identity、Plan/Entitlement 与 Xray/VLESS Credential mapping reconciliation；allowance、upgrade effective time 与无法确认的 credential 仍由 Admin/manual fallback 管理。

## Subscription flow

- Admin 为已确认 User 登记可发行的 per-user Xray/VLESS entries；AnyTLS entries 被拒绝进入 production projection。
- Portal 使用 short-lived/opaque User access token 读取自己的 view；Admin 使用独立 runtime admin token 查看 fleet summary。
- Subscription response 为 `text/plain` Base64 lines，兼容 V2rayN/V2rayNG；`Cache-Control: no-store`，不写访问日志中的 secret payload。
- `sub.enrpiglink.top` 继续是 public delivery boundary。现有 Worker 的 Plan-level KV behavior 作为 legacy compatibility reference；MVP per-user projection 需要新的受保护 origin/Worker integration，不直接把旧 static Plan path 当作 User authority。

## Failure and isolation

| Failure | Customer view | Proxy data plane |
| --- | --- | --- |
| Stats API unavailable | 标记 source gap / stale / Unknown | 不受影响 |
| Credential mapping missing | 标记 Unresolved，不计入 User total | 不受影响 |
| Collector/control plane down | 保留旧 ledger，显示 freshness state | 不受影响 |
| Counter reset/replay | 开启新 `counter_epoch`，用 idempotency 防重复 | 不受影响 |
| Subscription projection unavailable | 显示 delivery unavailable，保留已发 credential | 不受影响 |

## Deployment shape

第一版是一个独立 Python process + SQLite file + static Portal assets，配合 Windows protected control plane 上的 automatic collector process。它不依赖 Xray/sing-box startup，也不监听或修改 proxy ports。public HTTPS 由现有 Cloudflare boundary/reverse proxy 提供；collector 的 OS supervisor 只负责进程存活，不承载产品 scheduling。

## Deferred

- hard quota enforcement、blocking、throttling、automatic downgrade；
- billing settlement、proration、automatic upgrade allowance；
- DediRock Stats API remediation；
- AnyTLS precise accounting；
- Clash output；
- product scheduling、fleet orchestration、provider automation、Operation subsystem。
