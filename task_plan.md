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
