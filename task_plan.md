# SparkLink Production MVP Vertical Slice

## 目标

在不破坏现有 proxy data plane 的前提下，交付可审计的最小 production vertical slice：
`User → Entitlement → Subscription → Traffic → Metering → Portal view`。

## 阶段

- [x] Phase 1 — 读取 Source of Truth，审计当前代码/部署边界并冻结 rollback point
- [x] Phase 2 — 完成最小 production architecture decision 与 requirements reconciliation
- [x] Phase 3 — 实现 User/Credential、Plan/Entitlement、Subscription、Usage schema 与服务
- [x] Phase 4 — 接入真实 Xray metering，明确 unknown/error 与 data-plane isolation 语义
- [x] Phase 5 — 实现 Portal/Admin 最小视图，复用并验证既有 subscription delivery boundary
- [x] Phase 6 — 测试、部署、迁移/人工 fallback 与 production verification
- [x] Phase 7 — 文档、审计摘要、public Cloudflare deployment 与最终交付 checkpoint

## 不可越界

- 不继续扩展 `sparklink-deployer`，除非出现阻塞真实 Node operation 的 P0/P1 defect。
- 不修改现有 VMISS、DediRock、RackNerd 的 production proxy data plane。
- `hypro02` 仅记录为 `PREMIUM / CONDITIONAL`；不描述为 fully-qualified Premium。
- AnyTLS 保持 installed/standby capability，未经 reliable per-user accounting 验证不得进入 production Subscription。
- 不把 secrets、UUID、subscription token、private key、password 或其他 private runtime material 写入 Git。
- 不为未裁决的 proration、upgrade allowance 等规则自行发明业务语义；允许 manual-admin fallback。

## 当前状态

当前 Phase 1–7 已完成。Candidate Architecture 继续保留为 Proposal，不得直接当作已批准 To-Be Architecture；AnyTLS、Allowance/upgrade semantics 与长期 `PREMIUM / CONDITIONAL` qualification 仍按既有 Open Question/operations boundary 处理。

## Production Hardening & Metering Completion — 2026-08-29

- [x] H1 — 在隔离环境验证 sing-box 1.13.16 AnyTLS per-user accounting 与 counter lifecycle；结论保持 `Promote` 或 `Deferred`，不静默修改 production Subscription。
- [x] H2 — 审计 Xray collector/ledger 的 counter reset、duplicate、restart、partial failure、freshness 与 coverage 语义，并补 regression tests。
- [x] H3 — 设计并实现最小 automatic collector service；保留 manual collector 作为 fallback，不扩展 Portal/payment/scheduler/product feature。
- [x] H4 — 在不改变 proxy data plane 的前提下完成受控部署、故障/重启验证、operations 文档和 rollback checkpoint。

### Hardening constraints

- AnyTLS 只有在真实隔离 traffic evidence 能完成 `Credential → User → Node → Resource Pool → Usage` attribution 后，才可提出 promotion；否则记录 `Deferred pending reliable metering`。
- Collector failure、unknown/unobservable usage、远端 node partial failure 均不得被表示为可信的 `0`，也不得影响 proxy data plane。
- 不把 current Candidate Architecture Proposal 直接升级为已批准 To-Be Architecture；实现选择必须保持最小、可审计、可回滚。
