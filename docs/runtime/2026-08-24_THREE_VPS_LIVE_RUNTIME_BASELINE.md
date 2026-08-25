# SparkLink Runtime Baseline — 2026-08-24 Three-VPS Live Read-Only Audit

| Field | Value |
| --- | --- |
| Snapshot date | 2026-08-24 evening MDT（remote log dates may be 2026-08-25 UTC） |
| Scope | RackNerd NY、DediRock LA、VMISS LA |
| Evidence type | Live read-only SSH audit with redacted structured output |
| Evidence authority | Higher than older project context for runtime structure and capability status |
| Snapshot status | Point-in-time baseline；不代表永久 current state |
| Secret boundary | UUID、credentials、tokens、private keys、passwords、panel secrets、private URLs and exact secret-bearing values omitted |

## Snapshot Meaning and Safety Boundary

本文件记录 2026-08-24 live audit 的脱敏事实。它用于 reconcile Architecture Discovery 的 As-Is context，不是最终 architecture，也不是 implementation plan。

本次 audit 未写入 remote files 或 configuration，未执行 stop/start/restart、安装/升级、firewall/DNS/route 修改或删除操作。后续若 runtime 发生变化，应以新的 dated audit 取代本 snapshot；不得把本文件中的 listener、client count、counter value 或 service state 当作永久 current state。

当本文件与较旧 project context 冲突时，针对 live runtime structure、listener、protocol、Stats API 和 identity capability，以本文件的 evidence date 2026-08-24 为准；旧信息保留为历史/legacy context，并标明其较旧时间边界。

## Executive Summary

当前不是一个统一的 User platform，而是三套独立的 VPS runtime，加上 Windows control plane 的手工/半自动 subscription assembly：

- RackNerd：3x-ui family panel + SQLite + x-ui-managed Xray；已验证 persistent client_traffics 与可用的本机 Xray Stats API。
- DediRock：systemd 直管 Xray、sing-box、Nginx；没有 x-ui/3x-ui database；Xray Stats API 当前缺失，sing-box AnyTLS 没有已验证的 per-user persistent accounting。
- VMISS：3x-ui family panel + SQLite + Xray Stats API；另有 sing-box AnyTLS、WireProxy、Nginx；当前存在两个 opaque static subscription files，每个文件包含两个 nodes。
- 三台没有中央 User directory、跨节点 quota ledger、统一 subscription-token service 或可见的 cross-VPS synchronization database。
- 三台 live Xray client 均有 email 字段；audit 使用 USER_REF_n 或短 hash 脱敏。该能力是 technical user-ref capability，不等于 Requirements 中长期稳定的 User identity。
- RackNerd 与 VMISS 的 Xray Stats API 已 verified available；DediRock 是当前 Xray statistics gap。RackNerd 的 AnyTLS per-user accounting 为 N/A — AnyTLS not present in verified live runtime；DediRock 与 VMISS 仍为 Unknown / unverified。

## Cross-VPS Runtime Baseline

| VPS | Runtime ownership | Live protocol/runtime structure at audit date | Client/user-ref capability | Metering source status |
| --- | --- | --- | --- | --- |
| RackNerd | x-ui-managed Xray 26.6.1；3x-ui family SQLite | VLESS REALITY TCP/443；Xray Hysteria UDP/443（业务备注 HY2/LOSSY）；legacy VLESS WS TCP/11275；native WireGuard/WARP bridge | Xray clients 有 email；live client credential short hashes 未观察到重复 | Xray Stats API verified；SQLite client_traffics persistent |
| DediRock | systemd-managed Xray 26.7.28 + sing-box 1.13.16 + Nginx | Xray REALITY TCP/443；Cloudflare/Nginx TCP/2053 → local XHTTP；sing-box ShadowTLS TCP/8443；sing-box AnyTLS TCP/9443 → local WARP bridge | Xray REALITY/XHTTP clients 有 email；AnyTLS 为 direct/warp auth users | Xray Stats API verified absent/current gap；AnyTLS per-user accounting Unknown |
| VMISS | x-ui-managed Xray 26.7.28 + sing-box 1.13.16 + WireProxy | Xray REALITY TCP/443；sing-box AnyTLS TCP/9443；Nginx TCP/2053 → static subscription files；当前无 8443、10000、HY2/WS inbound | Xray clients 有 email；AnyTLS 为 direct/warp auth users；live credential short hashes 未观察到重复 | Xray Stats API verified；SQLite client_traffics persistent；AnyTLS per-user accounting Unknown |

三台均有各自本机的 WARP/HyTru component；audit 未发现运行态共享的 credential database、跨 VPS rsync/scp 或 central synchronization job。共享的是技术模式、Cloudflare/WARP 外部依赖和 Windows control-plane code，不是运行态 User store。

## Per-Node Redacted Facts

### RackNerd

**Observed / Verified Fact — 2026-08-24 snapshot**

- Xray 26.6.1 由 x-ui 管理；effective config 与 SQLite client_traffics 均存在，SQLite integrity check 为 ok。
- Xray API 绑定本机 127.0.0.1:62789；真实 statsquery 成功返回 user metrics，包含 uplink/downlink。
- SQLite clients=12、client_traffics=12；其中 live configuration 实际为 10 个业务 clients，数据库还保留 disabled/history clients。
- in-443-tcp 为 3 个 VLESS REALITY/Vision clients；Hysteria UDP/443 为 6 个 clients；legacy VLESS WS/11275 为 1 个 client。
- 所有 live Xray clients 均有 email 字段；本次脱敏比较未观察到多个 live clients 重用同一 credential short hash。
- client_traffics 已有 per-client persistent up/down；这是 panel cumulative value，不是跨节点或 Customer Billing Cycle ledger。
- x-ui database 有本地 client/sub-id state，但没有中央 User directory、node group database 或 cross-node ledger。
- WARP 为 Xray native WireGuard + local bridge；未发现独立 sing-box、Nginx 或 WireProxy service。

### DediRock

**Observed / Verified Fact — 2026-08-24 snapshot**

- Xray 26.7.28、sing-box 1.13.16、Nginx 由 systemd 分别管理；没有 x-ui/3x-ui database。
- Xray reality-in 为 3 个 VLESS REALITY/Vision clients；xhttp-in 为 3 个 VLESS XHTTP clients，由 Nginx TCP/2053 承接 Cloudflare XHTTP ingress。
- sing-box 提供 ShadowTLS TCP/8443 与 AnyTLS TCP/9443；AnyTLS 有 direct/warp 两个 auth users，warp 进入本机 WARP bridge。
- Xray configuration 没有可用的 api/stats configuration；真实 Stats API 读取失败，当前 per-client Xray Stats API 为 Verified absent/current gap。
- Xray VLESS clients 均有 email 字段；本次脱敏比较未观察到多个 live Xray clients 重用同一 credential short hash。
- 没有已验证的 sing-box AnyTLS per-user byte counter 或 persistent accounting source。
- /var/lib/sparklink/delivery/ 中的 root-only delivery artifacts 是离线交付材料，不是已发现的 online user-level subscription API。
- Nginx TCP/2053 是 XHTTP origin boundary，不是 user subscription delivery boundary。

### VMISS

**Observed / Verified Fact — 2026-08-24 snapshot**

- Xray 26.7.28 由 x-ui 管理；sing-box 1.13.16 与 WireProxy/WARP component 由 systemd 管理。
- Xray API 绑定本机 127.0.0.1:62789；真实 statsquery 成功返回当前 user uplink/downlink metrics。
- SQLite clients=5、client_traffics=5；其中 4 个 active REALITY clients 与 1 个 disabled test client。
- Xray in-443-tcp 为 4 个 VLESS REALITY clients（3 个 Vision、1 个无 flow）。
- sing-box AnyTLS TCP/9443 有 direct/warp 两个 auth users；当前没有 ShadowTLS、HY2 或 WS inbound。
- Nginx TCP/2053 以 static root 发布两个 opaque subscription files；每个 file 的 Base64 payload 为 2 个 vless + anytls nodes。路径正文未写入本 baseline。
- 所有 Xray clients 均有 email 字段；本次脱敏比较未观察到 live Xray clients 重用同一 credential short hash。
- SQLite client_traffics 提供 persistent cumulative values；Xray Stats API 提供当前进程可见的 per-user metrics。两者均不是中央 Customer Billing Cycle ledger。
- nodes=0、outbound_subscriptions=0、client_groups=0、client_global_traffics=0、node_client_traffics=0；没有面板内的跨节点 User directory 或 node-sync table。

## Identity and Subscription Implications

**Verified capability**

- 三台 Xray client configurations 均具备 email/user-ref field。
- RackNerd 与 VMISS 具备本地 clients、client_inbounds、client_traffics 以及 per-client sub-id state。
- DediRock 的 Xray client refs 在 config 中可读，但没有 x-ui persistent traffic table 或 Xray Stats API。
- 现有 email、sub-id、AnyTLS auth user 和 protocol credential 是 technical configuration identifiers，不应直接当作 Requirements 定义的长期稳定 User。

**Verified absence**

- 没有中央 User directory。
- 没有统一的 cross-node mapping：User → Subscription → Credential → Node。
- 没有统一 subscription-token service、quota ledger、monthly reset ledger 或 per-User cross-VPS aggregation store。
- 没有证据表明相同 technical credential 被跨节点复用；这不等于已经存在统一 User identity。

## Statistics and Usage Source Matrix

| Source | RackNerd | DediRock | VMISS | Interpretation |
| --- | --- | --- | --- | --- |
| Xray Stats API | Verified available at local API | Verified absent/current gap | Verified available at local API | API availability is not yet a historical Usage ledger |
| Xray per-client email/user-ref | Verified | Verified | Verified | Technical attribution key；不等于 canonical User |
| x-ui client_traffics | Verified persistent | Not applicable | Verified persistent | Local cumulative panel state；需处理 reset/restart/reconciliation |
| sing-box AnyTLS per-user accounting | N/A — AnyTLS not present in verified live runtime | Unknown / unverified | Unknown / unverified | 不得以 auth-user count 或整机 bytes 推算 User Usage |
| Provider Usage/cycle source | Not part of audit | Not part of audit | Not part of audit | Remains Unknown |

## Legacy and Primary-Target Boundary

以下 facts 继续保留为 As-Is history，但不自动成为 Iteration #1 primary metering target：

- RackNerd 的 legacy VLESS WS path 虽在 2026-08-24 snapshot 中存在，但是否纳入第一阶段 observation surface 仍需 scope decision。
- VMISS 旧 context 中的 HY2/WS、8443、10000 等历史 path 已被本 snapshot 的当前 live structure 覆盖为“不在当前 live listener 中”；历史 retirement 事实仍保留作 legacy context。
- DediRock ShadowTLS、AnyTLS、Cloudflare XHTTP 是当前 runtime families，但是否作为第一阶段完整 accounting coverage 仍需 architecture/scope decision。
- Cloudflare Edge、WARP/HyTru、static subscription delivery 的可达性或 delivery 事实，不自动等于 User Usage source。

## Remaining Unknowns

以下内容在 2026-08-24 audit 后仍是 Unknown / Needs Verification：

- 当前 email/user-ref 如何映射到长期稳定的 real-world User。
- Node canonical identity、Resource Pool membership interval、replacement 和跨 period attribution。
- AnyTLS per-user byte boundary、direction、counter source 和 persistence。
- Xray API counter 在 process restart、counter reset、missing metric、delayed read 时的 reconciliation semantics。
- Customer Billing Cycle 的 timezone、granularity、traffic unit 与 historical accounting policy。
- VPS provider resource cycle 的 authoritative source、获取方式、单位与 reconciliation key。
- 当前 Cloudflare live Worker/Route/KV 的完整 control-plane state。
- 订阅 token、Plan、Entitlement 与实际 Node membership 的 authoritative mapping。

## Evidence Precedence Rule

后续 Architecture Discovery 应采用以下 precedence：

1. 针对 live runtime structure、listener、protocol、Stats API、client email/user-ref 和 local traffic source：以 2026-08-24 audit 为准。
2. 针对未在 audit 覆盖的 Product Rules、Requirements semantics、provider cycle policy 和 future scope：以 reviewed/committed Requirements 与 Product Owner decision 为准。
3. 较旧 project context 不删除；若与本 snapshot 冲突，标为 historical/legacy，并注明旧 evidence date。
4. 本 snapshot 过期后，必须创建新的 dated runtime baseline；不得静默修改日期或把旧 snapshot 改写为 current。

## Audit Limits

- Audit 未读取 provider portal/API/账单系统，因此 provider Usage/cycle 未被验证。
- Audit 未建立中央 User mapping、Quota Ledger 或 Customer Billing Cycle。
- Audit 未验证 DediRock/VMISS AnyTLS per-user accounting；RackNerd verified live runtime 中不存在 AnyTLS/sing-box runtime，因此该项为 N/A。
- Audit 未修改 production data plane，也未将任何 Stats API 或 local traffic table 接入新系统。
- 本文件不选择最终 Collector、database、API、Portal、subscription architecture 或 technology stack。
