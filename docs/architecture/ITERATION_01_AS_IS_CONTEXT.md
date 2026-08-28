# Training Iteration #1 — As-Is System Context

| Field | Value |
| --- | --- |
| Document status | Architecture Discovery — Existing-System Context |
| Iteration | Training Iteration #1 |
| Related baseline | `docs/requirements/ITERATION_01_USAGE_METERING.md` |
| Discovery boundary | 只记录现有上下文、已知事实、未知事项与 architecture inference；不定义最终 architecture |
| Runtime safety | 本文未连接或修改任何 VPS、production configuration 或 proxy data plane |

## Purpose and Evidence Boundary

本文件用于 Architecture Design 前的 As-Is context discovery。目标是说明现有 traffic path、Node/runtime/protocol families、identity 与 subscription configuration 的分布、可见的 statistics source、provider-side usage 信息，以及需要保护的 production data plane。

本文不是全面的 SparkLink audit。2026-08-24 三 VPS live read-only audit 已作为 dated runtime baseline 纳入 `docs/runtime/2026-08-24_THREE_VPS_LIVE_RUNTIME_BASELINE.md`；该 snapshot 对 live runtime structure、listener、protocol、Stats API 和 client email/user-ref capability 的 evidence authority 高于旧 project context，但不代表永久 current state。

旧 project context 的主要时间边界为 2026-08-12 前后完成的部署、订阅和 reboot acceptance 工作。若旧 context 与 2026-08-24 audit 冲突，以较新的 audit 为准；旧内容保留为 historical/legacy context。Provider 账单状态、Customer Billing Cycle policy 和 AnyTLS per-user accounting 未被该 audit 验证。

分类含义如下：

- **Observed / Verified Fact**：此前通过实际 client、service、listener、config、subscription 或 reboot checks 得到的事实；仍需注意其验证时间。
- **Known from Existing Project Context**：此前参与项目时形成的稳定上下文，但本轮未重新验证。
- **Unknown / Needs Verification**：当前缺少足够证据，不能作事实陈述。
- **Architecture Inference**：根据上述事实推导出的设计关注点，不是已决定的 architecture。

## 0. Live Runtime Baseline Precedence

**Observed / Verified Fact — 2026-08-24 audit**

- RackNerd 与 VMISS 的 Xray Stats API 在本机 `127.0.0.1:62789` 可用，并能返回 per-user uplink/downlink metrics；两者也有持久的 x-ui `client_traffics`。
- DediRock 当前没有可用的 Xray Stats API，也没有 x-ui/3x-ui traffic database；AnyTLS per-user accounting 仍为 Unknown / unverified。
- RackNerd、DediRock、VMISS 的 live Xray client 都具备 email/user-ref 字段；这证明 technical attribution capability，不证明已经存在统一的 real-world User identity。
- 三台 runtime、protocol 和 listener structure 以该 audit 为当前 As-Is baseline；详见 dated runtime document。旧路径若不在该 snapshot 的 live structure 中，只保留为 legacy fact。

本节不选择最终 observation boundary、Collector、database、API 或 technology stack。

## 0.1 Project-Level Runtime and Metering Policy

**Project-Level Decision**

- `Xray` 是 SparkLink-wide production primary；`sing-box` 是 DR / technology hedge。
- strategic role / desired policy 与 live observed runtime state 必须分开记录；该 policy 不覆盖 2026-08-24 audit 的 observed facts。
- Iteration #1 primary metering observation surface 是 Xray production paths；sing-box accounting 保留为 extension boundary。
- 若后续 evidence 证明某 sing-box path 当前承载 production user traffic，必须显式记录 coverage gap 并作出纳入计量的 decision。
- Metering 是旁路 observational capability，不成为 production proxy data path 的 inline dependency；metering failure 不得导致 data plane failure。

上述是 project-level policy，不是对当前三 VPS live runtime 的重新分类。

## 1. Current User Traffic Paths

### 1.1 Common client-side path

**Known from Existing Project Context**

现有 User traffic 的共同前段可概括为：

`User client → v2rayN profile/subscription selection → local Xray or sing-box core → selected remote entry → VPS/data-plane runtime → optional relay/egress path → destination`

已知本地侧存在以下边界：

- v2rayN 负责 active profile、subscription group、local core lifecycle，以及 TUN 或 explicit SOCKS client path。
- 本地 core 可能是 Xray 或 sing-box；本地 `127.0.0.1:10808` 曾作为 local SOCKS/HTTP 诊断边界。
- active v2rayN database 与 isolated import/test database 是不同边界；此前验证尽量使用 isolated import，未将测试结果当作 active database 已刷新。
- subscription 返回的节点参数属于 delivery/configuration material，不等同于 User identity。

**Observed / Verified Fact**

此前本地 acceptance 能够按 selected profile 对 Native、HyTru、Reality、AnyTLS、XHTTP、HY2 等路径执行独立 TCP/UDP、reboot 或 import checks；这些结果证明对应测试时的 path 可用，不证明本轮仍处于相同 live state。

### 1.2 Native / direct VPS paths

**Known from Existing Project Context**

- RackNerd、DediRock 和 VMISS 均曾承载 Native/direct proxy paths；VLESS + REALITY TCP path 是主要 direct family 之一。
- RackNerd 的 HY2 曾被保留为 weak-network UDP fallback；这是一项既有 per-host exception，不应推导为所有 VPS 都使用 HY2。

**Observed / Verified Fact — 2026-08-24 audit**

- RackNerd live structure：VLESS REALITY TCP/443、Xray Hysteria UDP/443（业务备注 HY2/LOSSY）、legacy VLESS WS TCP/11275，以及 native WireGuard/WARP bridge。
- DediRock live structure：Xray REALITY TCP/443、Cloudflare/Nginx TCP/2053 → local XHTTP、sing-box ShadowTLS TCP/8443，以及 AnyTLS TCP/9443 → local WARP bridge。
- VMISS live structure：VLESS REALITY TCP/443、sing-box AnyTLS TCP/9443、Nginx TCP/2053 → static subscription files；当前没有 8443、10000、HY2/WS inbound。

上述 2026-08-24 structure 覆盖旧 context 对当前 listener/protocol 的推断；旧的 retired path 仍保留为 legacy fact。

**Architecture Inference**

Native path 的最直接 usage boundary 可能位于具体 VPS 的 protocol/runtime ingress 或其 per-user outbound accounting，但当前尚不能选择其中任何一个作为最终 metering boundary。

### 1.3 HyTru / WARP-backed paths

**Observed / Verified Fact**

此前验证过的 VMISS HyTru path 为：

`Xray or sing-box → loopback SOCKS 127.0.0.1:40000 → WireProxy 1.1.3 → Cloudflare WARP`

VMISS 还存在用于 health/status 的 loopback `127.0.0.1:40002`。该 path 的验证曾覆盖 TCP、SOCKS5 UDP、`warp=on`、soak、transfer 和 reboot recovery。

**Known from Existing Project Context**

- DediRock 的 HyTru 使用 Xray native WireGuard/WARP 与 local SOCKS bridge；它与 VMISS 的 WireProxy implementation 不是同一个 runtime layout。
- WARP 是 dynamic shared egress。它能说明 path/egress 状态，但不能将 Cloudflare exit 当作稳定的 Node identity 或 User identity。
- 同一 VPS 上的 Native 与 HyTru 可能共享部分 ingress/runtime，但其 egress 语义不同。

**Unknown / Needs Verification**

- 当前每条 HyTru traffic 是否有可按 User、Node、方向和 byte boundary 分解的 counter。
- WARP/WireProxy 层是否能可靠区分不同上游 User，而不是只能提供整个 bridge 的 aggregate。
- WARP/HyTru 的 per-user accounting、shared-egress attribution 和 counter semantics 仍未知。

### 1.4 Cloudflare Edge / subscription-entry paths

**Known from Existing Project Context**

- `VeilShift-Optimized` 是独立的 subscription/group capability，曾包含 1 个 DNS-auto 与 6 个 Cloudflare-entry VLESS nodes。
- Cloudflare Worker、custom domain、KV 和 exact Route 属于 subscription ingress/delivery 相关边界；它们不是 VPS exit 的同义词。
- Cloudflare Anycast entry address 不是固定 website-visible exit；历史多轮测试显示 exit 可能跨 rounds 变化。
- DediRock 还曾有 Cloudflare-proxied XHTTP path：client → Cloudflare entry → Nginx origin TCP/2053 → XHTTP/backend path。

**Observed / Verified Fact — 2026-08-24 audit**

- DediRock 当前确认存在 Cloudflare/Nginx TCP/2053 → local XHTTP 的 live path。
- VMISS 当前确认由 Nginx TCP/2053 静态发布两个 opaque subscription files；这属于 delivery boundary，不是已验证的中央 User service。
- 本次 audit 未重新读取 Cloudflare control plane，因此 Worker/Route/KV 的完整 current state 仍为 Unknown。

**Architecture Inference**

Cloudflare Edge、Cloudflare-proxied XHTTP 与 VPS data plane 之间存在至少两个可观察但不等价的 boundary：

1. entry/request boundary；
2. backend Node/protocol traffic boundary。

在没有确定 byte semantics 和 attribution correlation 前，不能把 Cloudflare request bytes 直接当作 User Usage 或 Node Usage。

### 1.5 Protocol families known in the existing system

**Observed / Verified Fact — 2026-08-24 audit**

| Family | Existing context | As-Is caution |
| --- | --- | --- |
| VLESS + REALITY | RackNerd、DediRock、VMISS 的 2026-08-24 live structure 均存在 | client email/user-ref 可见；统一 User attribution 尚不存在 |
| AnyTLS | DediRock、VMISS 当前 live 在 TCP/9443；各自存在 direct/warp auth users | per-user accounting 仍 Unknown / unverified |
| XHTTP | DediRock 当前由 Cloudflare/Nginx TCP/2053 承接 local XHTTP | ingress bytes 与 end-to-end User Usage 的关系未知 |
| ShadowTLS + Shadowsocks 2022 | DediRock 当前 live 在 TCP/8443 | 是否为 Iteration #1 primary metering target 仍需 scope decision |
| Hysteria2 / HY2 | RackNerd 当前 live 为 Xray Hysteria UDP/443；VMISS 当前无 HY2/WS inbound | 历史 VMISS retirement path 不自动成为 primary target |
| VLESS WS | RackNerd 当前 live 存在 legacy TCP/11275；VMISS 当前无 WS inbound | legacy path 是否纳入第一阶段仍未决定 |
| Cloudflare Edge VLESS | 旧 VeilShift-Optimized context 保留 | Cloudflare entry 不等于 fixed exit 或 concrete provider Node；当前 control plane 未重新验证 |

## 2. Current Node, Runtime, and Resource Context

### 2.1 Infrastructure Resource and Node context

**Known from Existing Project Context**

- 主要 VPS provider/host context 包括 RackNerd、DediRock、VMISS。
- `Infrastructure Resource` 表示 Provider 购买资源；`Node` 表示 SparkLink operational identity。换 IP、OS reinstall 或 runtime update 不创建新的 Node；替换为另一份 Provider resource 时创建新的 Node，旧 Node 为 `Retired`，Pool membership/history 保留。
- 同一 VPS 上可能存在多个 protocol listeners、relay paths 或 runtime processes；“一台 VPS”不自动等于一个可计量 Node，也不表示 live audit 已建立 canonical Node identity。
- `Native`、`HyTru`、`Origin`、`Edge`、`Basic`、`Plus` 和 `VeilShift-Optimized` 是既有 route、subscription 或 capability labels，不应直接当作 User、Credential 或 stable concrete Node identity。
- 既有 Basic/Plus subscription 曾使用 strict-superset contract；历史 Node counts 随 protocol retirement、独立 subscription 和 membership refresh 变化。

**Observed / Verified Fact — 2026-08-24 audit**

| Infrastructure Resource context | Current runtime baseline | Identity/statistics evidence |
| --- | --- | --- |
| RackNerd | x-ui-managed Xray 26.6.1；3x-ui SQLite | Xray email/user-ref、persistent `client_traffics`、Stats API available |
| DediRock | systemd-managed Xray 26.7.28 + sing-box 1.13.16 + Nginx | Xray email/user-ref；Stats API absent；AnyTLS per-user accounting unknown |
| VMISS | x-ui-managed Xray 26.7.28 + sing-box 1.13.16 + WireProxy | Xray email/user-ref、persistent `client_traffics`、Stats API available |

2026-08-24 audit 是当前 Infrastructure Resource/runtime structure 的 baseline；它没有建立 canonical Node identity。细节见 `docs/runtime/2026-08-24_THREE_VPS_LIVE_RUNTIME_BASELINE.md`。

**Unknown / Needs Verification**

- 当前 baseline 对每个 Infrastructure Resource 的 provider ID、assigned Node canonical ID、pool membership interval、protocol membership 和 replacement history 没有记录。
- 当前 Basic、Plus、VeilShift-Optimized 的 canonical User mapping 与 active v2rayN membership 未在 audit 中建立；Node/runtime snapshot 本身已按 2026-08-24 audit 更新。
- Node 从 Standard/Premium 或其他 future Resource Pool 归属变化时，历史 Usage 的归属规则未知。

### 2.2 Runtime components

**Observed / Verified Fact — 2026-08-24 audit**

三 VPS audit 直接确认的 runtime families 包括：

- local client：v2rayN、local Xray、local sing-box；
- VPS proxy core：Xray、sing-box；
- HTTP/transport ingress：Nginx；
- WARP bridge：WireProxy 1.1.3，及 Xray native WireGuard/WARP path；
- subscription/delivery side：Cloudflare Worker、KV、custom domain/Route；
- local diagnostic boundary：TUN、explicit SOCKS，以及 local health/incident tooling。

**Known from Existing Project Context**

- VMISS 的 x-ui-managed Xray config 曾位于 `/usr/local/x-ui/bin/config.json`，相关 SQLite `xrayTemplateConfig` 也曾需要同步维护；VMISS sing-box binary 位于 `/usr/bin/sing-box`。
- DediRock 的 Xray、sing-box、Nginx、certbot 和 firewall/Cloudflare-scoped paths 曾分别进行 route isolation 与 reboot validation；2026-08-24 audit 确认其仍为 systemd-managed runtime family。
- 这些 config/runtime locations 是现有 operations context，不是本轮要修改或读取的 target。

**Unknown / Needs Verification**

- runtime counter 的 retention、粒度、reset/restart semantics，以及是否跨 core/protocol 可对齐。
- DediRock Xray Stats API gap 的后续补足方式；本轮不选择 implementation。
- Cloudflare control plane 的 current Worker/Route/KV state。

## 3. User Identity, Credential, and Subscription Configuration

### 3.1 User identity

**Observed / Verified Fact — 2026-08-24 audit**

三台 live Xray client 均有 email/user-ref 字段；RackNerd 与 VMISS 还存在本地 x-ui client/sub-id/traffic state，DediRock 则只有 Xray config 中的 client refs。audit 未发现中央 User directory、统一 cross-node mapping 或以长期稳定 User 为唯一主键的 runtime source。

因此，2026-08-24 audit 将“technical user-ref capability”从 Unknown 升级为 Verified，但没有把它升级为 Requirements 定义的 User identity。

**Architecture Inference**

如果要满足 FR-01、FR-09 和 FR-10，后续 architecture 需要明确“User identity 与 technical access material 如何关联”，但不能把 UUID、subscription token 或其他 Credential 本身直接当作 User identity。

### 3.2 Credential and protocol identity

**Known from Existing Project Context**

Credential/private runtime material 曾分散在以下角色中：

- v2rayN active database、subscription import 和 local profile；
- VLESS UUID、REALITY key material、AnyTLS auth material、HY2 password 等 protocol-specific fields；
- VPS-side Xray/sing-box effective configs、x-ui database/template fields；
- Cloudflare Worker/KV/Route binding 与 subscription delivery configuration；
- private delivery artifacts 与 local deployment/validation manifests。

**Observed / Verified Fact — 2026-08-24 audit**

- RackNerd 与 VMISS 的 x-ui SQLite 具备 `clients`、`client_inbounds`、`client_traffics` 与 per-client `sub_id` state。
- DediRock 没有 x-ui/3x-ui database；其 Xray clients 可读，但没有 persistent panel traffic table。
- audit 未观察到 live clients 跨节点重用同一 credential short hash；这不等于已有统一 User lifecycle。

本文不记录任何实际 credential value、UUID、token、private key 或 password。

**Unknown / Needs Verification**

- 哪些 technical Credential 当前对应哪个 real-world User。
- Credential rotation、revocation、expiry 与 User/Subscription lifecycle 的实际 source of truth。
- 一个 User 是否可以同时拥有多个 Subscription、多个 Credential、多个 Node access path，以及这些关系的 effective period。

### 3.3 Subscription configuration

**Known from Existing Project Context**

- Basic、Plus 与 VeilShift-Optimized 曾分别作为 subscription/group capability 管理。
- subscription payload 曾需要满足 strict Base64、protocol/count contract、strict-superset 和 isolated v2rayN import acceptance。
- subscription delivery 与 VPS data plane 可以分离；Cloudflare Worker/KV/custom Route 的问题曾导致 subscription ingress failure，而不必然代表每个 VPS Node failure。
- active v2rayN database 的刷新是 client-side state change，不能以“subscription refresh completed”替代对实际 active records 的验证。

**Observed / Verified Fact — 2026-08-24 audit**

- VMISS 当前以 Nginx TCP/2053 静态发布两个 opaque subscription files，每个包含两个 `vless + anytls` nodes。
- DediRock 的 Nginx TCP/2053 是 XHTTP origin，不是 user-level subscription API；DediRock delivery artifacts 是 root-only static/offline material。
- Windows control-plane assembly 与 Cloudflare Worker/KV code 仍属于 existing capability/context；本次 audit 未重新验证 Cloudflare live control plane。

**Unknown / Needs Verification**

- 当前 subscription payload、x-ui sub-id 与 technical email/user-ref 如何映射到 stable User；audit 只验证了字段存在，没有验证其业务身份含义。
- Subscription、Plan、Entitlement、Credential 和 concrete Node membership 的当前 authoritative mapping。
- subscription delivery 的 request/access logs 是否保留、是否能按 User 归因、是否覆盖 direct/manual/imported configurations。

## 4. Existing Traffic Counters and Statistics Sources

本节只记录现有 source 的可见性与不确定性，不选择 Collector、database 或具体 technology stack。

| Existing component / boundary | Classification | Known capability | Metering relevance / limitation |
| --- | --- | --- | --- |
| v2rayN local database and profile | Known from Existing Project Context | 保存 local profiles、subscription groups 和 imported node configuration | 能说明 client configuration state；不等于 authoritative User Usage counter |
| local Xray / sing-box logs | Observed / Verified Fact | 曾用于 core lifecycle、errors、path diagnostics 和 acceptance | 仍不是 canonical per-User historical Usage source |
| VPS Xray — RackNerd | Observed / Verified Fact — 2026-08-24 audit | 本机 Xray Stats API available；x-ui `client_traffics` persistent；email/user-ref 可读 | 仍需处理 process restart、counter reset、retention 与 cross-node aggregation |
| VPS Xray — VMISS | Observed / Verified Fact — 2026-08-24 audit | 本机 Xray Stats API available；x-ui `client_traffics` persistent；email/user-ref 可读 | API/current-process metrics 与 SQLite cumulative values 的 reconciliation 未决定 |
| VPS Xray — DediRock | Observed / Verified Fact — 2026-08-24 audit | Xray email/user-ref 可读；Xray Stats API verified absent/current gap | 不能声称 DediRock per-client Xray metering 已可用 |
| VPS sing-box AnyTLS | Unknown / Needs Verification | DediRock、VMISS 当前有 `direct`/`warp` auth users | AnyTLS per-user accounting、stable byte boundary 和 persistence 仍未验证 |
| Nginx | Observed / Verified Fact | DediRock 当前承载 Cloudflare-proxied XHTTP；VMISS 当前承载 static subscription delivery | request bytes 是否等于 end-to-end User Usage、是否重复计算、是否能关联 backend Node Unknown |
| WireProxy / WARP bridge | Observed / Verified Fact | 曾提供 loopback SOCKS、health endpoint、TCP/UDP/WARP path checks | 已知 health/path evidence 不等于 per-User traffic statistics；shared egress 还可能改变 website-visible exit |
| Cloudflare Worker / Edge ingress | Known from Existing Project Context | 曾承载 subscription ingress 或 Edge VLESS entry capability | Cloudflare request/transfer statistics 的可获得性、粒度、retention、User/Node attribution Unknown |
| Provider portal / host metrics | Unknown / Needs Verification | 本轮没有读取任何 provider portal 或 API | 不能据此声称已有 provider Usage source |

**当前结论**

2026-08-24 audit 将 RackNerd 与 VMISS 的 Xray per-user statistics source 从 Unknown 升级为 Verified available，并将 DediRock 的 Xray Stats API 明确为 Verified absent/current gap。但目前仍没有跨三 VPS、跨 Xray/sing-box、跨 Nginx/WireProxy/WARP、Cloudflare Edge 和 provider side 的统一 Usage source；已知 local counters 仍不自动满足 FR-09 的 immutable Usage observation 要求。

## 5. Provider-Side Usage and Cycle Information

**Known from Existing Project Context**

- RackNerd、DediRock、VMISS 的 provider resource/accounting 属于与 Customer Billing Cycle 分离的 infrastructure-side context。
- 此前工作关注 VPS availability、listener、traffic path、reboot recovery 和 provider/network behavior；没有建立 provider Usage ingestion 或 cycle normalization。
- Provider cycle 不应从 customer subscription cycle 或某个 Node label 推断。

**Observed / Verified Fact — 2026-08-24 audit**

- 本次 audit 未读取任何 provider portal、provider API、invoice 或 resource-cycle export。
- 因此 provider Usage/cycle source、周期单位、reconciliation key 与 correction behavior 仍为 Unknown；live audit 的 freshness upgrade 不延伸到 provider-side accounting。

**Unknown / Needs Verification**

- 各 VPS provider 的 authoritative resource cycle source（portal、invoice、API、export 或其他）。
- cycle 的 timezone、start/end semantics、resource granularity、traffic/transfer unit 和 retention。
- provider-side traffic 是否按 VPS、public IP、resource package、interface、direction 或其他 dimension 统计。
- provider records 与具体 Node、Resource Pool、Customer Billing Cycle 的 reconciliation key。
- provider data delayed、corrected、missing 或 changed 时的 audit/reconciliation behavior。

## 6. Production Data Plane That Must Be Protected

以下属于必须保护的 production data plane 或其直接依赖；本轮不对其做任何 change：

**Observed / Verified Fact / Known from Existing Project Context**

- User client 到 remote proxy path 的 existing v2rayN → local core → VPS/runtime traffic；
- RackNerd 的 Native paths、当前 Xray Hysteria UDP/443（业务备注 HY2/LOSSY）与 native WARP bridge；
- DediRock 的 Xray REALITY、Nginx/XHTTP、sing-box ShadowTLS/SS2022、AnyTLS 和相关 Native/HyTru paths；
- VMISS 的 VLESS REALITY、AnyTLS、static subscription ingress、以及 HyTru/WireProxy/WARP bridge；2026-08-24 audit 确认当前无 8443、10000、HY2/WS inbound；
- Xray、sing-box、Nginx、WireProxy/WARP 等 active listeners、service dependencies、certificates 和 firewall/Cloudflare bindings；
- Basic/Plus existing subscription contracts 与 VeilShift-Optimized independent subscription ingress；
- active v2rayN database、TUN/explicit SOCKS client path，以及用户当前的 local profile state。

**Architecture Inference**

Metering/management plane 必须以旁路、可降级和可恢复为设计约束；任何需要修改 active proxy runtime、reload production config、改变 subscription contract 或改变 client database 的方案，都必须另行获得明确授权并通过独立 change review。这是对 FR-17、NFR-03 的保护性推论，不是最终 architecture 选择。

## 7. Candidate Observation Boundaries — Discovery Only

以下只是后续 Architecture Design 可比较的 observation boundary，不表示选择：

1. **Client/profile boundary**：观察 selected Subscription、Credential reference、local session 或 client-side bytes；Subscription 只能作为 correlation evidence，不能作为 Usage attribution authority；主要问题是 User attribution 与 local-only visibility。
2. **Protocol ingress boundary**：Iteration #1 primary surface 是 Xray production paths；可在 Xray 的 per-user/per-inbound boundary 观察 bytes。2026-08-24 已证明 RackNerd/VMISS Xray Stats API 可读、DediRock Xray Stats API 缺失；sing-box accounting 保留 extension boundary，主要问题仍是不同 protocol/runtime 的 statistics semantics 与 counter availability。
3. **Transport/relay boundary**：在 Nginx、XHTTP、WireProxy/WARP 或 Cloudflare Edge 观察 request/transfer；主要问题是重复计量、加密层不可见、shared egress 和 end-to-end attribution。
4. **Serving Node / Infrastructure Resource boundary**：在 serving Node、Infrastructure Resource 或 service listener 汇总 Usage；主要问题是同一 VPS 多 runtime、多 protocol、Node replacement 和 Resource Pool membership。
5. **Provider boundary**：观察 provider resource/accounting records；主要问题是 Customer Usage 与 Infrastructure Usage 的 reconciliation，而不是 User-level proxy attribution。
6. **Reconciliation boundary**：比较多个 source 的 totals、timestamps 和 known gaps；这是可能的 observability concern，不代表已决定建立某种 database 或 Collector。

## 8. Requirements Open Questions: Blocking vs Deferred

### 8.1 Directly blocking the next Architecture Design

以下仅是会直接影响 Metering architecture topology 的问题；它们不等同于所有后续 implementation/accounting policy 问题：

| Requirements question | Why it blocks |
| --- | --- |
| Xray production paths 与 sing-box/legacy paths 的具体 coverage boundary | 已决定 Xray 是 primary surface，但仍需明确具体 production path 的 coverage 与 gap 表达方式；不能把 strategic role 当作 live runtime absence |
| Node canonical identity、Resource Pool membership interval 与跨 period attribution（对应 Open Question 3 的 topology 部分） | Node replacement 的 identity rule 已决定，但没有 Node identity interval 与 pool membership semantics，FR-08、FR-10、FR-15 仍无法定义完整历史聚合边界 |
| User-to-Credential/Subscription-to-Node mapping 放在哪个受控边界，以及 technical email/user-ref 如何关联 stable User | audit 只验证 per-node technical user-ref capability，没有中央 User directory；没有该 topology，FR-01、FR-09、FR-10 无法跨节点聚合 |
| Metering/management plane 与 existing proxy data plane 的隔离边界 | FR-17 与 NFR-03 要求 metering failure 不使 data plane unavailable；需要先确定观察关系和故障边界，才能继续设计 topology |

这些问题阻塞的是“可验证的 topology 与 primary boundary design”，不阻塞继续整理 candidate boundary、定义 adapter extension point 或设计只读 discovery checklist。

### 8.2 可以暂时 Deferred 的问题

以下问题对后续 implementation、accounting policy、reconciliation 或 operations 重要，但可以在 topology 确定后通过 extension point Deferred：

- Open Question 1：Standard/Premium numeric quota 或 allowance values；本 Iteration 不执行 hard quota enforcement。
- Open Question 2：upgrade、downgrade、proration、renewal 和 pricing rules；不影响先定义 observation boundary。
- Open Question 4：VPS provider resource cycle 的 authoritative source 与 acquisition method；可先保留 provider adapter/reconciliation extension point，不阻塞 Customer Usage topology。
- Open Question 5：AnyTLS precise metering approach；AnyTLS 可先作为未验证 protocol adapter，不能声称已覆盖，但不阻塞 Xray topology discovery。
- Open Question 6：customer billing-cycle timezone、start/end rule、reporting granularity；属于 period/accounting policy，不阻塞 boundary topology。
- Open Question 7：traffic units、rounding、counter reset、duplicate/out-of-order rules；属于 counter/reconciliation policy，不阻塞 topology。
- Open Question 8：retention、correction、audit 和 privacy policy；在进入 production storage/operations design 前必须补齐。
- Open Question 9 的完整 identity lifecycle（creation、merge、deactivation、legal/data-retention）；mapping boundary 本身需要先决定，但完整 lifecycle policy 可 Deferred。
- Open Question 10：availability、delay、reconciliation 和 recovery targets；可在 boundary 和 failure-isolation 初步明确后定量化。
- Collector、database、API、Portal、subscription system、LB 和 automatic scheduling 的具体 technology selection；它们属于后续 architecture/implementation decisions，不应在本 As-Is document 中预先决定。

Deferred 不代表已解决；这些问题仍保持 `Unknown / Needs Verification` 或 Requirements 中的 `Open Question — Undecided` 状态。

## 9. Discovery Conclusion

当前最重要的 As-Is 事实是：SparkLink 已有多个 production protocol/runtime 和多个 subscription/entry/egress path，但尚未有一个经过验证的、统一的 User-to-Node-to-period traffic counter source。

因此，下一步 Architecture Design 应先围绕 identity attribution、Node identity/membership、counter semantics、provider reconciliation 和 failure isolation 建立可审查的候选边界；本文件不选择最终 Collector、database、API、runtime hook 或具体 technology stack。
