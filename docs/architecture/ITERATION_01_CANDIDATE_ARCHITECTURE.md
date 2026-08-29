# Training Iteration #1 — Candidate Architecture

| Field | Value |
| --- | --- |
| Document status | Candidate Architecture — Product Owner review pending |
| Baseline commit | `32d4915` |
| Scope | User Identity + Usage Metering / Observability |
| Decision status | 本文比较候选方案，不确定最终 To-Be Architecture |
| Runtime safety | 本文未连接或修改任何 VPS、production configuration 或 proxy data plane |

## Purpose and Evidence Boundary

本文基于已 committed 的 [Requirements](../requirements/ITERATION_01_USAGE_METERING.md)、[As-Is Context](ITERATION_01_AS_IS_CONTEXT.md)、[2026-08-24 Runtime Baseline](../runtime/2026-08-24_THREE_VPS_LIVE_RUNTIME_BASELINE.md) 与 [project-level ADR](../decisions/) 提出候选 architecture。本文不实现 Collector、database、API、Portal、Operation subsystem 或 Control Plane。

2026-08-24 runtime snapshot 仍是 live runtime structure 的 evidence authority：RackNerd 与 VMISS 的 Xray Stats API 及 persistent `client_traffics` 已 verified；DediRock 的 Xray Stats API 是 verified absent/current gap；DediRock 与 VMISS 的 sing-box AnyTLS per-user accounting 仍未验证。Strategic policy（`Xray = production primary`、`sing-box = DR / technology hedge`）不覆盖这些 observed facts。

## Common Architectural Invariants

所有候选方案都必须满足以下已经裁决的约束：

- `Infrastructure Resource != Node`。同一 Provider resource 的换 IP、OS reinstall 或 runtime update 不创建新 Node；替换为另一份 resource 时创建新 Node，旧 Node 为 `Retired`，Pool membership/history 保留。
- conceptual relationship 为 `Plan → Entitlement → Resource Pool → serving Node(s)`，不建立直接的 `Plan → Node` serving authority。
- runtime technical identity 通过 `Credential/User mapping` 归因到 stable `User`；`Subscription` 是 projection，不是 Usage attribution authority。
- Usage observations 必须 append-preserving。新的 `Customer Billing Cycle`、Plan/Entitlement/Allowance 变化都不得删除、清零或重写既有 Usage。
- Customer Usage 与 Infrastructure Usage 是不同的 aggregation perspectives。Provider resource cycle 不由 Customer Billing Cycle 推导。
- Iteration #1 primary observation surface 是 Xray production paths。sing-box、provider usage 和其他 transport sources 必须作为 extension/gap boundary 表达。
- Metering 是旁路 observational capability，不成为 proxy data path 的 inline dependency；任何 source unavailable、mapping unavailable 或 metering component failure 都不得使 data plane unavailable。
- 不执行 hard quota enforcement，不因 metering gap blocking、throttling、downgrade 或改变现有 proxy configuration。

## Common Logical Boundaries

以下是用于比较 topology 的 logical boundaries，不是最终 component 或 technology selection：

1. **Runtime observation**：读取 Xray production runtime 的 per-user/per-inbound statistics 或 persistent counters。
2. **Attribution**：保存 runtime technical identity 与 Credential/User mapping 的关系及其 effective time。
3. **Node/Pool history**：按 observation time 解析 Node、Infrastructure Resource 与 Resource Pool membership。
4. **Historical evidence**：保留 source reading、observation time、period context、traffic amount、coverage/gap status 与 provenance。
5. **Aggregation views**：从历史 evidence 生成 Customer Usage 与 Infrastructure Usage；两者不互相替代。
6. **Extension boundary**：未来可以增加 sing-box runtime adapter、provider usage adapter 或其他 source，而不改变既有 Xray evidence 的语义。

候选方案可以改变上述 boundaries 的部署位置和处理时序，但不能删除这些逻辑职责。

## Candidate A — Central Read-Only Pull

### Topology shape

一个位于 management/metering side 的 central read-only observation component，定期读取各 Node 的 Xray statistics source，随后将 observation 送入 attribution、history 和 aggregation boundaries。Node 上不新增 inline metering component。

```text
Xray Stats API / x-ui client_traffics
          ↓ read-only pull
Central Metering Observation
          ↓
Credential/User attribution + Node/Pool history
          ↓
Append-preserving Usage evidence
          ↓
Customer Usage / Infrastructure Usage views
```

### Required behavior

- RackNerd、VMISS：Xray Stats API 用于取得当前 per-user statistics；x-ui persistent `client_traffics` 用于 restart recovery、历史累计值对照和 reconciliation。两者都不单独被假定为 Customer Billing Cycle ledger。
- DediRock：Xray Stats API gap 必须作为明确的 unavailable/coverage-gap state 记录；不得把无数据解释为 zero，也不得用整机 bytes 推算 per-User Usage。
- attribution 在 central observation boundary 通过 `runtime technical identity → Credential → User` 解析。必须按 observation/effective time 使用 mapping，不能用当前 mapping 静默重写历史。
- observation 同时携带 Node、Infrastructure Resource 和当时的 Resource Pool membership context。IP、OS 或 runtime 更新继续使用原 Node；Provider resource replacement 产生新 Node，旧 Node 进入 `Retired`。
- Customer Usage 按 User、Entitlement、Customer Billing Cycle、Node 与 Resource Pool 聚合；Infrastructure Usage 按 Infrastructure Resource、Node 和 provider cycle 聚合。Provider cycle 不要求与 Customer Billing Cycle 对齐。
- historical Usage 采用 append-preserving 逻辑；新的 cycle 或 Plan change 只影响后续 view，不删除 source evidence。

### Isolation and failure behavior

- Pull component 只读取既有 observation boundary，不进入 Xray、sing-box、Nginx、WireProxy/WARP 或其他 proxy forwarding path。
- central component unavailable 时，已有 proxy data plane 继续服务；Metering view 出现 freshness lag。RackNerd/VMISS persistent counters 可在恢复后用于 reconciliation，但 counter reset、missing metric 或 source discontinuity 必须标记 gap，不能静默补零。
- DediRock 的 Xray path 继续运行；该 Node 的 per-user coverage 显式显示 gap，直到未来 source adapter 被批准和验证。

### Extension boundary

sing-box adapter 可作为同一 observation contract 的后续 source，但不能自动继承 Xray 的 counter semantics。Provider adapter 只产生 Infrastructure Usage evidence，不反向产生 User Usage 或 Customer Billing Cycle attribution。

### Main trade-off

这是最小 operational footprint 的候选方案，适合先利用已验证的 RackNerd/VMISS Xray sources；代价是 central pull availability、remote read access、polling interval 与 counter reconciliation 成为主要 correctness/recovery concerns。

## Candidate B — Node-Local Asynchronous Observation Buffer

### Topology shape

每个 Node 或对应 Infrastructure Resource 有一个与 data plane 分离的 local observation adapter。它读取本机 Xray statistics source，将 observation 与 source timestamp 暂存，再异步发送到 central history/aggregation boundary。

```text
Node-local read-only adapter
  ├─ Xray Stats API
  └─ x-ui client_traffics
          ↓ asynchronous push
Central Usage intake / history
          ↓
Credential/User attribution + Node/Pool history
          ↓
Customer Usage / Infrastructure Usage views
```

### Required behavior

- RackNerd、VMISS local adapter 同时读取 Xray Stats API 与 persistent `client_traffics`，保留 source identity 和 local observation time，再异步传递；central boundary 不把任一 source 当作绝对 authority。
- DediRock local adapter 可以报告 Xray Stats API unavailable/current gap，但不得为缺失的 per-user source 生成 synthetic zero 或整机分摊值。若未来出现新的 Xray source，应以新的 evidence/status 进入 reconciliation，而不是覆盖 gap history。
- local adapter 至少保留 runtime technical identity 和 Credential reference；User attribution 可在 central intake 完成，但 mapping 必须带 effective time。mapping unavailable 时，observation 暂存为 unresolved，不丢弃。
- local adapter 读取的 Node 与 Resource Pool context 必须以 observation time 为准；同一 Infrastructure Resource 的 runtime maintenance 不创建新 Node，resource replacement 产生新 Node/Retired history。
- central aggregation 从 append-preserving evidence 生成 Customer Usage 与 Infrastructure Usage；local buffer 不自行决定 Plan、Entitlement、quota 或 billing policy。

### Isolation and failure behavior

- local adapter 不位于 proxy forwarding path，不被 Xray/sing-box service dependency 所依赖，不执行 quota enforcement。
- central intake unavailable 时，local buffer 可以保留未发送 observations，恢复后再提交；因此对短时 management-network outage 的 evidence continuity 优于 Candidate A。
- local adapter、buffer 或 local disk failure 不得停止 proxy data plane，但会造成对应 Node 的 observation gap；该 gap 必须可见且不可伪装为 zero。
- source counter reset、process restart 或 buffer replay 可能产生 duplicate/out-of-order inputs；history boundary 必须保留 source provenance 并交由后续 reconciliation policy 处理。

### Extension boundary

sing-box adapter 可以按 Node-local runtime family 增加；provider adapter 仍放在独立的 Infrastructure Usage boundary，不能因为 local adapter 存在而获得 Customer Usage authority。

### Main trade-off

该方案将 recovery responsibility 下沉到每个 Node，降低 central outage 对 evidence freshness 的影响；代价是每个 production host 增加一个需独立升级、监控、权限控制和容量评估的 management-plane footprint。它不解决 DediRock 当前缺失 per-user source 的根本问题。

## Candidate C — Evidence-First Raw Observation and Delayed Attribution

### Topology shape

该方案把“source evidence 保存”和“User attribution / usage views”明确拆成两个时序阶段。Runtime adapter 可以采用 central pull 或 node-local acquisition，但第一落点是 immutable raw observation boundary；之后再按 Credential/User、Node/Pool 和 cycle context 生成 derived views。

Runtime observation surface 仍限定为 Xray production paths：RackNerd/VMISS 的 per-user Xray statistics sources，以及 DediRock 当前已知但缺少 Stats API 的 Xray production path。Candidate C 的实际差异不在 source 的读取位置，而在于 raw evidence 先于 attribution 成为独立的历史边界。

```text
Xray runtime source adapters
          ↓
Immutable raw observation boundary
  (source, runtime identity, Node, time, counters, gap/provenance)
          ↓ asynchronous attribution
Credential/User mapping + Node/Pool history
          ↓
Customer Usage view  ∥  Infrastructure Usage view
```

### Required behavior

- RackNerd、VMISS 的 Xray Stats API 与 x-ui persistent `client_traffics` 作为不同 provenance 的 raw sources 保存；API 适合 current observation，persistent counter 适合 cumulative/recovery evidence，但两者的 reconciliation 不在 source ingestion 时被静默决定。
- DediRock 的 Xray Stats API gap 作为 raw coverage interval/status 保存。没有 per-user source 时不生成 User Usage，不将缺失解释为 zero，也不以 Nginx/WARP/整机 aggregate 替代 Xray per-user attribution。
- raw observation 先保留 runtime technical identity、Credential reference（如可得）、Node、Infrastructure Resource、source time、observation time、counter/traffic value 与 provenance。`User` attribution 可以延迟到 mapping evidence available 后生成。
- Credential rotation、User mapping change、Plan/Entitlement change 或 Subscription regeneration 只影响后续 attribution/view；raw evidence 与已完成的 historical Usage 不重写。
- Node 与 Pool history 按 observation/effective time 进行解析。旧 Node 的 Retired 状态和旧 Pool membership 继续作为 historical context，新 Provider resource 使用新 Node。
- Customer Usage 与 Infrastructure Usage 从 shared raw evidence 生成但保持独立的 projection；provider cycle source 后续加入时只扩展 Infrastructure Usage reconciliation。

### Isolation and failure behavior

- raw observation、attribution 和 aggregation 都位于 data plane 之外；attribution 或 view generation failure 不影响 existing proxy forwarding。
- source adapter unavailable 时只形成 coverage gap；raw evidence boundary 或 attribution boundary unavailable 时，proxy data plane 继续运行。
- attribution boundary unavailable 时，raw observations 可先保留为 unresolved，Customer Usage view 延迟；恢复后可按 provenance replay，而不重新读取或篡改旧 runtime history。
- 该方案最能区分“没有 source evidence”“有 evidence 但尚未 mapping”与“view 暂不可用”三类状态，便于 audit 和 recovery；代价是需要更清晰的 raw/derived lifecycle 与 reconciliation policy。

### Extension boundary

sing-box、provider 或其他未来 source adapter 只需提供 raw evidence contract 和 capability/coverage status，不得直接写入 Customer Usage。新 adapter 的 semantic coverage 必须单独验证；若 sing-box path 被证明承载 production user traffic，必须显式建立 coverage gap 与纳入计量的 decision。

### Main trade-off

该方案对 late mapping、counter disagreement、source gap 与 audit replay 的 correctness/recoverability 最强，但 logical boundaries 最多，implementation 和 operations 复杂度最高；它仍不要求选择 database、framework 或具体 deployment technology。

## Trade-off Analysis

| Criterion | Candidate A — Central Pull | Candidate B — Node-Local Buffer | Candidate C — Evidence-First |
| --- | --- | --- | --- |
| Correctness | High：直接读取 per-user Xray source；依赖 polling、delta 与 effective-time mapping 正确 | High：保留 local observation time；需处理 buffer replay、duplicate 与 reset | Highest：raw evidence 与 derived attribution 分离，最不易因 mapping/source 变化重写历史 |
| Production isolation | Highest：不增加 Node-local runtime component，central path 不进入 forwarding | High：adapter 不 inline，但增加 Node-local management footprint | High：逻辑隔离最清晰；若 acquisition 采用 local adapter，仍有 B 的 footprint concern |
| Evidence coverage | Medium：RackNerd/VMISS Xray 可用；DediRock 保持 per-user gap | Medium：可改善 central outage 下的 freshness，但不补足 DediRock source gap | Medium：source coverage 不变，但 gap、provenance 与 unresolved evidence 最完整 |
| Implementation complexity | Low–Medium：central acquisition、mapping、history、aggregation | Medium–High：每 Node adapter、buffer、delivery/replay 与 rollout | High：raw evidence、attribution pipeline、derived views 与 replay/reconciliation |
| Operational complexity | Medium：central health、remote read access、polling/reconciliation | High：per-Node lifecycle、权限、容量、buffer backlog 与升级 | High：多阶段 freshness、raw retention、replay、mapping 与 view health |
| Extensibility | Medium–High：新增 adapter 集中在 central observation boundary | High：可按 runtime family 放置 local adapter，但 rollout 面更广 | Highest：sing-box/provider 等 source 可复用 raw evidence boundary，并独立声明 semantics |
| Recoverability / auditability | Medium–High：依赖 persistent source 与 central checkpoints | High：local buffer 提供 source outage recovery，但需处理 buffer loss/replay | Highest：raw provenance、gap、late mapping 与 derived view 可分别审计 |

## Cross-Candidate Failure Rules

无论采用哪个候选方案，以下 failure behavior 都不能改变：

- Xray Stats API、x-ui persistent counter、future adapter 或 provider source unavailable 时，状态必须是 unavailable/gap/unknown，而不是 silently zero。
- Metering component、mapping component、aggregation view 或 management network failure 不得停止、重载或修改 proxy data plane。
- Counter reset、process restart、delayed observation、duplicate 或 out-of-order input 不得导致已记录历史被静默重写；应保留 provenance 并等待明确 reconciliation policy。
- DediRock 当前 per-user Xray statistics gap 不能通过 auth-user count、整机 bytes、Nginx bytes 或 WARP bytes 推算 User Usage。
- `Subscription`、Plan 或当前 Credential state 不能覆盖 observation-time 的 Credential/User、Node/Pool 和 period context。

## Scope and Non-Decisions

本文没有决定：

- central pull、node-local push 或 evidence-first 方案中的任何一个为 To-Be Architecture；
- Collector、database、API、Portal、framework、programming language、deployment mechanism 或 storage schema；
- AnyTLS precise metering、sing-box production coverage、provider cycle acquisition、billing/pricing/quota policy；
- Control Plane registration、fleet orchestration、Operation subsystem 或 automatic scheduling；
- 任何 VPS、production configuration、existing subscription contract 或 client database 的修改。

## Product Owner Review Questions

1. Candidate A、B、C 中，哪种 evidence placement 与当前 production isolation / maintenance capacity 最匹配？
2. 是否接受将 unresolved Credential/User mapping、source gap 与 delayed Usage view 作为显式状态，而不是静默丢弃或补零？
3. Iteration #1 是否按 Xray production paths 建立第一阶段 coverage matrix，并将 DediRock per-user gap 作为 acceptance blocker、deferred adapter gap 或其他状态？
4. 是否保留 Candidate C 的 raw evidence / derived view separation，即使最终 acquisition topology 采用 A 或 B？

在 Product Owner 对上述 trade-offs review 并确定 To-Be 之前，本文不授权任何 implementation 或 runtime change。
