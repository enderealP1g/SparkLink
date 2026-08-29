# ADR-0004: Production MVP vertical slice topology

| Field | Value |
| --- | --- |
| Status | Accepted — Product Owner execution authorization, 2026-08-29 |
| Scope | User identity、Usage Metering、最小 Portal、Subscription delivery 与 operations fallback |
| Related requirements | `FR-01`–`FR-18`、`NFR-01`–`NFR-07` |

## Decision

### Control-plane shape

Production MVP 采用一个小型、single-process `Control Plane`，内部逻辑分为：

```text
User / Plan / Entitlement
        ↓
Credential → User mapping + Node / Resource Pool history
        ↓
Xray raw observations → append-preserving Usage ledger
        ↓                         ↓
Customer Usage view       Infrastructure Usage view
        ↓
Portal / Subscription delivery
```

第一版使用 SQLite 作为 durable store。这里的选择服务于当前小规模、可审计和可回滚的 production MVP，不代表未来必须使用同一 storage technology。

Control Plane 与 proxy data plane 通过独立 process、独立 local port、独立 runtime directory 和显式 reverse-proxy boundary 分离。Xray、sing-box、Nginx、WireProxy 不依赖 Control Plane 的可用性。

### Identity and history

- `User` 使用 SparkLink 自己生成的 stable opaque identity。
- runtime technical identity 只作为 `Credential`，通过 `Credential → User` mapping 参与 attribution；UUID、password 或 subscription token 不作为 User primary identity。
- `Plan`、`Entitlement`、`Allowance`、`Subscription` 变化只影响后续 view/发行，不重写已有 Usage。
- Node/Pool membership 使用 effective time 保存；`hypro02` 记录为 `PREMIUM / CONDITIONAL`，VMISS 为当前 Premium resource。其他未纳入正式 service surface 的 Node 不得被静默计入 Customer Usage。

### Metering

- `Xray` production paths 是当前 primary observation surface。
- 已验证的 Xray Stats API 以 read-only pull 方式读取；counter reset 通过 `counter_epoch` 与 idempotency key 隔离。
- Windows protected control plane 上的 automatic collector/ingest 作为正常路径，one-shot manual collector 作为 operations fallback；SSH credentials 不放到 VPS。Collector failure 只形成 freshness/coverage state，不停止 proxy data plane。具体 lifecycle hardening 见 [`ADR-0005`](0005-metering-hardening-and-automatic-collection.md)。
- source unavailable、unresolved mapping、stale observation 与 coverage gap 显示为 `Unknown`/明确状态，不显示为 zero。
- DediRock 当前 Xray Stats API gap 不通过整机 bytes、Nginx bytes 或 AnyTLS bytes 推算 User Usage。

### Subscription and Plan operations

- Subscription 是由当前 User、Entitlement、Pool/Node membership 和已登记 Credentials 生成的 projection。
- 第一版只发行已完成 per-user attribution 的 Xray/VLESS paths；AnyTLS 保持 installed/standby capability，但在 reliable accounting 前不进入 production Subscription。
- `V2rayN` / `V2rayNG` compatible Base64 subscription 是 Must；Clash Deferred。
- Portal 只允许创建 upgrade request；downgrade 不支持。涉及 proration、立即生效时间或 allowance 变化的请求进入 `manual-admin review`，系统不自行发明计价规则。
- 既有 `sub.enrpiglink.top` Worker 作为 delivery boundary 继续复用/演进；当前 Worker live state 未被假设为可用，未完成 control-plane credential reconciliation 前不声称 public cutover 已完成。

### Capacity status

`hypro02` 可以在 `PREMIUM` Pool 中以 `CONDITIONAL` 状态承载当前 Plus users。该状态不是 fully-qualified Premium；Portal/Admin 必须保留该状态和后续 capacity/reputation/stability observation。

## Consequences

- MVP 可以提供真实 User/Usage/Subscription 闭环，同时不需要 microservices、event bus、policy DSL 或 product scheduler。
- One-shot manual collector、manual User/Credential mapping 和 manual upgrade review 是有意保留的 operational fallback；automatic collector 的 Windows OS supervisor 不是产品业务调度能力。
- 若 Control Plane 或 metering unavailable，用户现有 proxy data plane 继续运行，但 usage freshness、coverage 或 subscription view 可以明确显示 unavailable。
- `spark.enrpiglink.top` 与 `sub.enrpiglink.top` 的最终 public binding 仍需要受保护的 Cloudflare control-plane access；缺失时只能完成 local/QQG staging，不能伪造上线状态。

## Non-Decisions

本 ADR 不决定 future Control Plane fleet orchestration、hard quota enforcement、billing/proration policy、DediRock Stats API remediation、AnyTLS accounting、provider cycle acquisition、Clash delivery 或具体长期 hosting strategy。

## Source references

- [Iteration #1 Requirements](../requirements/ITERATION_01_USAGE_METERING.md)
- [MVP Requirements Reconciliation](../requirements/ITERATION_01_PRODUCTION_MVP_RECONCILIATION.md)
- [ADR-0001](0001-project-domain-identity-and-serving-relationships.md)
- [ADR-0002](0002-project-boundaries-and-operations.md)
- [ADR-0003](0003-iteration-01-runtime-and-metering-boundary.md)
- [2026-08-24 Runtime Baseline](../runtime/2026-08-24_THREE_VPS_LIVE_RUNTIME_BASELINE.md)
- [`hypro02` Deployer Acceptance](../operations/2026-08-29_HYPRO02_DEPLOYER_ACCEPTANCE.md)
