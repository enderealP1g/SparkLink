# 2026-08-29 Production MVP Control Plane Operations Record

| Field | Value |
| --- | --- |
| Status | QQG origin operational；Cloudflare Worker public edge deployed and accepted |
| Snapshot | 2026-08-29 point-in-time operations evidence |
| Related decision | [`ADR-0004`](../decisions/0004-production-mvp-vertical-slice.md) |
| Scope | `User → Entitlement → Subscription → Traffic → Metering → Portal view` |

本文件记录本次 Production MVP vertical slice 的运行事实和回滚点，不代表永久 current state。所有 future operations 应重新验证 live state。

## Deployment topology

```text
Windows manual collector --SSH read-only--> Xray Stats API on Nodes
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

## Observed implementation state

- QQG 上的 `sparklink-control-plane.service` 已启用并监听 `127.0.0.1:8080`。
- QQG runtime verified 为 Xray `26.7.28`、Nginx `1.24.0`、WireProxy `1.1.3`；sing-box `1.13.16` 保持 stopped/standby。
- QQG Xray `StatsService` 仅绑定 `127.0.0.1:62789`；Xray 443、CDN WebSocket 10080、Nginx 和 WireProxy listeners 保持可用。
- SQLite database 位于受保护的 runtime directory；admin token、User portal token 和 subscription URI 不进入 Git。
- 当前采用 manual User/Credential migration。User allowance 仍未裁决，因此 Portal 显示为 unknown/未配置而不是虚构数值。
- 已登记的正式 subscription 只包含 6 条 `vless` entries：RackNerd Standard、VMISS Premium 和 hypro02 Premium；AnyTLS、DediRock reference path 与 CDN standby identities 未进入正式 User subscription。

## Verification evidence

| Area | Evidence |
| --- | --- |
| Code | local `compileall` + 8 unit tests passed；QQG remote test run 7/7 passed |
| Xray config | `xray run -test` passed；Stats API loopback query succeeded；post-reboot listener restored |
| Services | reboot 后 Xray、Nginx、WireProxy、Control Plane 均 `active` |
| Collector | RackNerd/VMISS/hypro02 两次采样均成功 ingest；RackNerd 的 unrelated unmapped observations 保持 unresolved，不污染当前 User view |
| Usage | public edge acceptance 后，manual Plus User 的 `STANDARD` coverage 为 `available`、used 为 `0`；`PREMIUM` coverage 为 `available`、used 为 `1223310` bytes；total 为 `1223310` bytes；相对此前 `611798` checkpoint 增长 `611512` bytes |
| HTTP origin | Nginx TLS path 的 Portal、Bearer `/api/me`、Bearer `/subscription` 均返回成功；subscription 为 6 行、全部 `vless` |
| Client paths | 公网 subscription response 解码为 6 条 `vless` entries；从其中派生的 isolated Xray client 实际启动两条 hypro02 Xray/VLESS REALITY path 并形成 non-zero Stats counters。两条 path 的 OpenAI/Anthropic 返回 `401`、Gemini `403`、Google AI `200`、Google `204`。此前 Gate B 的 Native policy-level `403` 负面 evidence 仍保留，不能被这些 no-key probes 覆盖。 |
| Reboot | reboot 后历史 ledger 仍为 `611798` bytes；new counter epoch 重新可采集，未清零历史 Usage |

## Public edge status

QQG origin path 继续提供 `/sparklink-mvp/` reverse proxy，并保留原 CDN VLESS exact path。新的 edge Worker source 位于 [`cloudflare/sparklink-edge-worker.js`](../../cloudflare/sparklink-edge-worker.js)。2026-08-29 通过 Wrangler OAuth 部署 `sparklink-edge`，version 为 `14dc2663-6e41-4993-bbf8-cbdc4849401f`，`CONTROL_PLANE_ORIGIN` 作为受保护 deployment variable 配置。

Wrangler 的 Custom Domain 方式因目标 hostname 已存在 externally managed DNS record 而被 Cloudflare 拒绝；未删除或改写 DNS。保留现有 proxied DNS 后，仅为 `spark.enrpiglink.top/*` 与 `sub.enrpiglink.top/*` 创建 Workers Route，旧的 `sparklink-subscriptions` Worker 和其他 route 未修改。

公网 acceptance 已通过：`spark.enrpiglink.top/` 与 `/healthz` 返回 `200`；带受保护 User token 的 `/api/me` 返回当前 `Plus` User、Billing Cycle、两 Pool 的 `available` coverage 及真实 Usage；`sub.enrpiglink.top/u/<portal-token>` 返回 `200`，Base64 解码后为 6 条 V2rayN/V2rayNG-compatible `vless` entries。错误 token 返回 `401`，错误路径返回 `404`，未输出任何 token 或 subscription material。

Worker 只位于 management/subscription delivery boundary；QQG proxy listeners 和 `sparklink-control-plane` 保持独立，Worker/Portal failure 不成为现有 proxy data plane 的 inline dependency。

## Rollback points

- Xray Stats change 的 pre-change backup：`/var/backups/sparklink-control-plane/stats-try-20260829T031517Z/config.json`。
- Nginx management path 的 pre-change backup：`/var/backups/sparklink-control-plane/nginx-20260829T033112Z.conf`。
- rollback 前必须恢复原 file owner/group/mode，运行 `xray run -test` 或 `nginx -t`，再 restart/reload 并验证 443、10080、62789、8080。
- SQLite historical Usage 不通过 rollback 删除；任何 schema/data rollback 必须先复制受保护 database backup，并保留 append-preserving ledger。
- Cloudflare Worker rollback point 为 version `14dc2663-6e41-4993-bbf8-cbdc4849401f`；需要回退时使用受保护 Wrangler OAuth 执行 version rollback，并保留当前 Workers Route 配置记录。不要删除 Worker 作为 rollback。

## Known gaps and manual fallback

- `hypro02` 仍是 `PREMIUM / CONDITIONAL`，长期 IP reputation、sustained throughput、stability 和 provider routing qualification 继续由 operations 观察。
- Allowance 数值、upgrade pricing/proration 和 automatic upgrade effective time 未裁决；当前 upgrade request 进入 manual-admin review。
- DediRock Stats API 仍为 coverage gap；不得用 host/Nginx totals 代替 User Usage。
- AnyTLS per-user accounting 未形成可靠 evidence；不得进入 production Subscription，也不得把不可观测 traffic 记为 0。
- Windows manual collector 是当前 operations fallback；若 collector/Control Plane 暂时不可用，保留旧 ledger 并显示 freshness/coverage unknown。
- Cloudflare public edge 已完成本轮 acceptance；后续 edge 变更仍须使用受保护 Wrangler OAuth，并限定在本项目两个 hostname。
