# 2026-08-29 Production MVP Control Plane Operations Record

| Field | Value |
| --- | --- |
| Status | QQG origin、Cloudflare Worker public edge 与 identity/cycle reconciliation operational |
| Snapshot | 2026-08-29 point-in-time operations evidence |
| Related decision | [`ADR-0004`](../decisions/0004-production-mvp-vertical-slice.md)、[`ADR-0005`](../decisions/0005-metering-hardening-and-automatic-collection.md) |
| Scope | `User → Entitlement → Subscription → Traffic → Metering → Portal view` |

本文件记录本次 Production MVP vertical slice 的运行事实和回滚点，不代表永久 current state。所有 future operations 应重新验证 live state。

## Deployment topology

```text
Windows automatic collector --SSH read-only--> Xray Stats API on Nodes
                                      |
                                      v
                          QQG loopback Control Plane
                          127.0.0.1:8080 + SQLite
                                      |
                 Nginx TLS path: hypro02-cdn:2053/sparklink-mvp/
                                      |
                        Cloudflare Worker `sparklink-edge` boundary
                         spark.enrpiglink.top / sub.enrpiglink.top
```

Control Plane 是独立 process，不是 Xray、Nginx 或 WireProxy 的 inline dependency。Stats、collector 或 Portal failure 不应停止 proxy data plane。

本次 QQG 记录为 `hypro02`，属于 `PREMIUM / CONDITIONAL` candidate Node；`CONDITIONAL` 不等同于 fully-qualified Premium。`Xray` 是当前正式 production observation surface；AnyTLS 仅保留为 installed/standby capability。

身份与周期边界已按 Product Owner 裁决落地：SparkLink 使用 stable `User` identity；Portal access token 与独立的 `Subscription` token 分离，`Subscription` 仍是 projection。`Customer Billing Cycle` 使用 `Asia/Shanghai` 的 15→15 policy；VPS instance local timezone 仅用于 `Provider Resource Cycle` metadata，两者不互相推断或替代。当前正式 user-facing protocol surface 为可可靠归因的 Xray/VLESS paths。

## Observed implementation state

- QQG 上的 `sparklink-control-plane.service` 已启用并监听 `127.0.0.1:8080`。
- QQG runtime verified 为 Xray `26.7.28`、Nginx `1.24.0`、WireProxy `1.1.3`；sing-box `1.13.16` 保持 stopped/standby。
- QQG Xray `StatsService` 仅绑定 `127.0.0.1:62789`；Xray 443、CDN WebSocket 10080、Nginx 和 WireProxy listeners 保持可用。
- SQLite database 位于受保护的 runtime directory；`users` 只保存 Portal/Subscription token hash，plaintext 只通过受保护 operator delivery bundle 短暂存在，不进入 Git、日志或 operations docs。
- 当前采用 manual User/Credential migration。User allowance 仍未裁决，因此 Portal 显示为 unknown/未配置而不是虚构数值。
- 当前已完成六个 stable `User` 的 identity reconciliation；legacy runtime credentials 保留历史映射，managed Xray/VLESS credentials 作为后续 subscription projection 的 source mapping。历史 Usage 未因 identity、Plan 或 cycle reconciliation 被重写。
- 已登记的正式 subscription projection 只包含完成 mapping 的 Xray/VLESS entries：RackNerd Standard、VMISS Premium 和 hypro02 Premium；AnyTLS、DediRock reference path 与 CDN standby identities 未进入正式 User subscription。

## Verification evidence

| Area | Evidence |
| --- | --- |
| Code | local `compileall` + 8 unit tests passed；QQG remote test run 7/7 passed |
| Xray config | `xray run -test` passed；Stats API loopback query succeeded；post-reboot listener restored |
| Services | reboot 后 Xray、Nginx、WireProxy、Control Plane 均 `active` |
| Collector（initial vertical-slice snapshot） | RackNerd/VMISS/hypro02 两次采样均成功 ingest；RackNerd 的 unrelated unmapped observations 保持 unresolved，不污染当前 User view |
| Automatic collector hardening（initial snapshot） | Windows `Task Scheduler` 通过 SSH tunnel 连接 QQG loopback Control Plane；protected runtime secret 修正后连续两个 interval 均为 3/3 Nodes ingest、`failed=0`，Task 保持 `Running`；post-identity current status 见下文 |
| Usage | public edge acceptance 后，manual Plus User 的 `STANDARD` coverage 为 `available`、used 为 `0`；`PREMIUM` coverage 为 `available`、used 为 `1223310` bytes；total 为 `1223310` bytes；相对此前 `611798` checkpoint 增长 `611512` bytes |
| HTTP origin | Nginx TLS path 的 Portal Bearer `/api/me` 与独立 Subscription header `/subscription` 均返回成功；subscription 为 6 行、全部 `vless` |
| Client paths | 公网 subscription response 解码为 6 条 `vless` entries；从其中派生的 isolated Xray client 实际启动两条 hypro02 Xray/VLESS REALITY path 并形成 non-zero Stats counters。两条 path 的 OpenAI/Anthropic 返回 `401`、Gemini `403`、Google AI `200`、Google `204`。此前 Gate B 的 Native policy-level `403` 负面 evidence 仍保留，不能被这些 no-key probes 覆盖。 |
| Reboot | reboot 后历史 ledger 仍为 `611798` bytes；new counter epoch 重新可采集，未清零历史 Usage |

## Public edge status

QQG origin path 继续提供 `/sparklink-mvp/` reverse proxy，并保留原 CDN VLESS exact path。新的 edge Worker source 位于 [`cloudflare/sparklink-edge-worker.js`](../../cloudflare/sparklink-edge-worker.js)。2026-08-29 通过 Wrangler OAuth 部署 `sparklink-edge`，当前 deployment 已激活，`CONTROL_PLANE_ORIGIN` 作为受保护 deployment variable 配置。

Wrangler 的 Custom Domain 方式因目标 hostname 已存在 externally managed DNS record 而被 Cloudflare 拒绝；未删除或改写 DNS。保留现有 proxied DNS 后，仅为 `spark.enrpiglink.top/*` 与 `sub.enrpiglink.top/*` 创建 Workers Route，旧的 `sparklink-subscriptions` Worker 和其他 route 未修改。

公网 acceptance 已通过：`spark.enrpiglink.top/` 与 `/healthz` 返回 `200`；带受保护 User token 的 `/api/me` 返回当前 `Plus` User、Billing Cycle、两 Pool 的 coverage 及真实 Usage；`sub.enrpiglink.top/u/<subscription-token>` 返回 `200`，Base64 解码后为 6 条 V2rayN/V2rayNG-compatible `vless` entries。Worker 将 public subscription token 转为内部 `X-SparkLink-Subscription-Token` header，origin 不把它误当作 Portal token。错误 token 返回 `401`，错误路径返回 `404`，未输出任何 token 或 subscription material。

同一公网 response 由全新 isolated v2rayN `7.18.0` 实例通过其 subscription updater 获取成功并生成 6 个 `VLESS/REALITY` profiles，其中包含 2 个 hypro02 profiles。该实例未启动本地代理服务，因为 `10808` 已被现有 live v2rayN 占用；实际外连验证由临时 isolated Xray client 完成，避免触碰 live client。

Worker 只位于 management/subscription delivery boundary；QQG proxy listeners 和 `sparklink-control-plane` 保持独立，Worker/Portal failure 不成为现有 proxy data plane 的 inline dependency。

## Token issuance and owner acceptance checkpoint

2026-08-29 已补齐 Admin-only token issuance/rotation/delivery workflow，详细 runbook 见 [`2026-08-29_TOKEN_ISSUANCE_DELIVERY.md`](2026-08-29_TOKEN_ISSUANCE_DELIVERY.md)。Control Plane 已从 legacy `users.subscription_token` plaintext storage 迁移为 `portal_token_hash` + `subscription_token_hash`，并完成 schema、foreign-key、业务计数和 service health verification。

root `usr_plus_manual_01` 的 Portal token 已通过受保护 Windows operator bundle 交付并完成真实 Portal acceptance：`root`、`Plus`、`OWNER`、`legacy-pre-baseline` Customer Cycle、`Asia/Shanghai` 以及独立 `STANDARD`/`PREMIUM` pool rows 均已从 live `/api/me` 与 Portal 页面复核；`/api/me` self-scope 通过。当前 root Subscription URL 未被 rotate，迁移后仍通过独立 Subscription path 验证。其它五个 User 未被自动 rotate；Admin metadata list 已确认六个 User 都可由同一 workflow 指定交付。

## Rollback points

- Xray Stats change 的 pre-change backup：`/var/backups/sparklink-control-plane/stats-try-20260829T031517Z/config.json`。
- Nginx management path 的 pre-change backup：`/var/backups/sparklink-control-plane/nginx-20260829T033112Z.conf`。
- rollback 前必须恢复原 file owner/group/mode，运行 `xray run -test` 或 `nginx -t`，再 restart/reload 并验证 443、10080、62789、8080。
- SQLite historical Usage 不通过 rollback 删除；任何 schema/data rollback 必须先复制受保护 database backup，并保留 append-preserving ledger。
- Cloudflare Worker rollback point 为当前已部署 version；需要回退时先通过受保护 Wrangler OAuth 的 deployment history 解析 version，再执行 version rollback，并保留当前 Workers Route 配置记录。不要删除 Worker 作为 rollback。

## Known gaps and manual fallback

- `hypro02` 仍是 `PREMIUM / CONDITIONAL`，长期 IP reputation、sustained throughput、stability 和 provider routing qualification 继续由 operations 观察。
- Allowance 数值、upgrade pricing/proration 和 automatic upgrade effective time 未裁决；当前 upgrade request 进入 manual-admin review。
- DediRock Stats API 仍为 coverage gap；不得用 host/Nginx totals 代替 User Usage。
- AnyTLS 已完成 dated isolated investigation；custom `v1.13.16 + with_v2ray_api` test build 可观察 `users[].name` per-user counters，但 installed official binary 不含该 API，且 reset/restart loss 与 durable mapping boundary 未解决。结论仍为 `Deferred pending reliable metering`；不得进入 production Subscription，也不得把不可观测 traffic 记为 0。详见 [AnyTLS accounting investigation](2026-08-29_ANYTLS_ACCOUNTING_INVESTIGATION.md)。
- Windows automatic collector 是正常路径；one-shot manual collector 是 operations fallback。若 collector/Control Plane 暂时不可用，保留旧 ledger 并显示 freshness/coverage unknown。其 lifecycle、dedupe、reset 与 stale semantics 见 [`Metering hardening record`](2026-08-29_METERING_HARDENING.md)。
- identity migration 后的最新 collection evidence 为 `hypro02` ingest、RackNerd/VMISS source reachable but no current per-user counter rows；后两者显示 `Unknown`，不是 zero。旧的 `3/3 ingest` 只代表 migration 前 snapshot。
- Windows automatic collector 的 admin secret 使用 ignored repository runtime 中的 `LocalMachine DPAPI` protected file；Task Scheduler 早期 `AppData` path 读取失败已通过 path correction 解决，旧失败行保留在本地 log 作为诊断 evidence。
- Cloudflare public edge 已完成本轮 acceptance；后续 edge 变更仍须使用受保护 Wrangler OAuth，并限定在本项目两个 hostname。
