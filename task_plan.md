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

## Production Identity, Subscription & Cycle Reconciliation — 2026-08-29

- [x] C0 — 将 Production Hardening checkpoint `ae1259f` push 到 `origin/main`。
- [x] C1 — 实现并测试 `Customer Cycle` 与 `Provider Resource Cycle` 的独立模型、timezone 和 legacy/pre-baseline semantics。
- [x] C2 — 完成六个真实 User 的 stable identity、role、Plan/Entitlement 和独立 token/subscription projection reconciliation。
- [x] C3 — 以 legacy coexistence、config test、reload/restart、isolated client 和 real traffic evidence 完成 Xray/VLESS Credential migration。
- [x] C4 — 部署、回归测试并验证 `Credential → User → Node → Resource Pool → Usage → Customer Cycle`，记录 provider metadata/unknowns 和 rollback point。

### Cycle constraints

- `Customer Cycle` 固定以 `Asia/Shanghai` 的 `2026-09-15 00:00` 为统一 policy baseline，周期为每月 15 日 00:00 至次月 15 日 00:00。
- `Provider Resource Cycle` 使用各实例已验证的 local timezone；provider contract/financial cadence 与 traffic reset authority 分开记录。
- 2026-09-15 之前的 Usage 只标记为 `legacy/pre-baseline`，不删除、清零、重写或纳入新 commercial cycle enforcement。
- 不从付款日期、购买日期、套餐描述或 Next Due 推断 provider traffic reset。

## Current hardening checkpoint

- Xray Stats probe 已修正为真正经过 SOCKS/VLESS 的 isolated transfer；QQG real managed User 产生 `baseline + delta`，不是绕过 proxy 的 false positive。
- Automatic collector 已切换为 PowerShell launcher + Python interval process；startup cleanup 只处理本 task 的 stale SSH forward，当前 task running。
- 当前 coverage 为 `hypro02=ingested`、RackNerd/VMISS=`Unknown`（source reachable but no current per-user rows），不把 unknown 记为 zero。
- AnyTLS 保持 `Deferred pending reliable metering`；Candidate Architecture 仍是 Proposal。

## Production Operations Token Issuance & Delivery — 2026-08-29

- [x] T0 — 暂停其它 Bug Hunt，确认当前 Control Plane/Portal、live DB schema、root identity 与受保护 Windows runtime 边界。
- [x] T1 — 将 Portal/Subscription token storage 收敛为 hash-only，并实现 Admin-only issue/rotate/revoke workflow。
- [x] T2 — 实现一次性 Windows protected delivery bundle 与显式 local-only clipboard helper；默认不向 stdout/chat 输出 plaintext。
- [x] T3 — 补齐 old/wrong token rejection、cross-kind separation、DB no-plaintext、ACL/ignored-path regression checks。
- [x] T4 — 备份并部署 Control Plane；只先 rotate root Portal token，生成 root bundle，独立验证新 token/旧 token和 public path。
- [x] T5 — 使用新 token 完成 `spark.enrpiglink.top` owner acceptance；随后为六个 User 建立 delivery bundle 机制，不自动 rotate 其他现有 token。
- [x] T6 — 记录脱敏运行证据；原 Bug Hunt 曾可恢复，但当前 Product Owner override 继续保持其暂停。

### Token safety gates

- Control Plane/SQLite 只保存 `portal_token_hash`、`subscription_token_hash` 与可选的 hash-only `subscription_token_legacy_hash`；legacy plaintext 列必须迁移并移除。
- plaintext 只在 issue/rotate 响应、可信 local bundle re-home 或一次性 local delivery bundle 中存在；不进入 Git、日志、operations docs 或聊天。
- Portal 默认立即 revoke 旧 token；Subscription staged migration 允许显式保留一个旧 hash，验收后可由 `revoke-legacy` 显式清除。
- Bundle 仅写入当前 Windows operator 可读的 ignored `runtime/delivery/<username>/delivery.json`；OWNER index 不含 secret；写入后验证 ignored、ACL、hash-only DB 和真实 public projection/rejection。

## Six-user delivery reconciliation — Product Owner override

- [x] R0 — 固定 scope 为 `root`、`Hegin`、`abing`、`dangbin`、`liuwen`、`zhanhao`，读取 live Plan/Entitlement/projection metadata。
- [x] R1 — Trusted runtime bundle 只复用；hash-only User 才 issue；Portal/Subscription 独立，旧 Subscription 以 grace hash 保留。
- [x] R2 — 写入六个受 ACL 保护的 per-user bundles 与 OWNER-only index，提供 `copy --user <username> --kind portal|subscription`。
- [x] R3 — 逐 User 验证 local auth、public `sub.enrpiglink.top` projection、Plan pool/count/protocol、Free not-configured 状态和 AnyTLS exclusion。
- [x] R4 — 完成六用户 self-scope/cross-user rejection、ignored/tracked/ACL/secret scan；Bug Hunt 继续暂停。
