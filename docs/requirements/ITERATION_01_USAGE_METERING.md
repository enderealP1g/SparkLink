# Requirements Specification: Training Iteration #1 — User Identity + Usage Metering

| Field | Value |
| --- | --- |
| Document status | Requirements baseline — reviewed and committed |
| Iteration | Training Iteration #1 |
| Scope | User identity concepts and usage metering/observability |
| Decision authority | Product Owner 对未决的产品与运营策略问题拥有裁决权 |
| Security boundary | Git documentation 中不得记录 credentials 或其他 secrets |

## Background

SparkLink 同时是一个学习项目和真实业务项目。本 Iteration 在进入 implementation 前，先建立可追溯的 requirements baseline 与统一的领域词汇。项目需要区分稳定的业务主体、商业产品包装、访问权益、技术 credentials、基础设施资源和已观测的 Usage。

本 Specification 描述 metering domain 的预期行为与边界，但不规定 database schema、Collector implementation、API、Portal、subscription delivery mechanism 或 deployment procedure。

## Goal

Training Iteration #1 的目标是建立可靠的 metering 与 observability 基础，使系统能够针对一个已知 period 回答：

1. 哪个 User 产生了 Usage，或被归因于该 Usage；
2. 哪个 Node 承载了该 Usage；
3. 在相关时间，Node 属于哪个 Resource Pool；
4. Usage 发生的时间及所属 period；以及
5. 如何按 User、Resource Pool 和 Node 聚合 Usage。

第一阶段只进行 observation。它必须保留历史事实，并且不得让现有 proxy data plane 依赖 metering availability。

## Domain Definitions

### User

`User` 表示长期稳定的现实业务主体。User 不等同于 Plan、Subscription 或 Credential。这些对象可以变化、到期或被替换，但不应因此改变其所对应的 User identity。

### Subscription

`Subscription` 表示与 User 相关联的服务订阅关系。Subscription 不等同于 User、Plan、Entitlement 或 Credential；本 Iteration 不定义其 implementation。

### Credential

`Credential` 表示用于访问或认证服务的技术凭据。Credential 可以创建、轮换、撤销或到期，其生命周期与 User 的长期 identity 生命周期分离；Credential 的替换不应产生新的 User。

### Plan

`Plan` 是包含一组预期 benefits 的产品包装。当前初始 Plan 集合为：

- `Free`
- `Basic`
- `Plus`

Plan 与 Entitlement 是两个概念。Plan 本身不等于某项已经实际拥有的 Usage right。

### Entitlement

`Entitlement` 表示 User 实际拥有的 resource access 或 usage rights。Entitlement 可以被授予、变更、暂停或结束，但这不改变 User 作为长期稳定业务主体的 identity。

### Allowance

`Allowance` 表示与 Plan 或 Entitlement policy 关联的 limit 或 included amount。Allowance 是后续 evaluation 使用的 policy input；本 Iteration 只观察 Usage，不执行 hard quota enforcement。

### Resource Pool

`Resource Pool` 是资源的逻辑分组，与任何单独的 Node 分离。当前至少区分以下 pool categories：

- `Standard`
- `Premium`

### Infrastructure Resource

`Infrastructure Resource` 表示由 Provider 购买并管理的具体基础设施资源。它承载 provider、cost、renewal、replacement 等 infrastructure lifecycle facts，不等同于 `Node`。

### Node

`Node` 表示由 SparkLink 分配给 Infrastructure Resource 的稳定 operational identity，能够用于表达承载 proxy traffic 的 serving identity。对同一 Infrastructure Resource 进行换 IP、OS reinstall 或 runtime update 不创建新的 Node；替换为另一份 Provider resource 时创建新的 Node，旧 Node 进入 `Retired`，其 Pool membership 与 history 保留。Node identity 与 pool membership 必须保持可区分。

### Usage

`Usage` 是在带有 timestamp 的 interval 或定义好的 reporting period 内，归因到某个 User 和具体 Node 的 resource consumption observation。runtime technical identity 应通过 Credential/User mapping 进行归因；`Subscription` 不是 Usage attribution authority。Usage 是历史 evidence，不是根据 User 当前 Plan 或 Entitlement 重新计算出的 view。

### Billing cycles

`Customer Billing Cycle` 是用于 customer usage 或 billing analysis 的 customer-facing period。`VPS provider resource cycle` 是用于 infrastructure resource accounting 的 provider-facing period。两者相互独立，不要求对齐。

## Functional Requirements

### Identity and commercial concepts

| ID | Requirement | Priority |
| --- | --- | --- |
| FR-01 | domain model 和 documentation 必须将 User 表示为长期稳定的现实业务主体，并使其与 Plan、Subscription、Credential 保持区分。 | Must |
| FR-02 | 初始支持的 Plan values 必须为 Free、Basic、Plus。 | Must |
| FR-03 | Plan 必须作为 product packaging concept 建模，并且必须与 Entitlement 保持区分。 | Must |
| FR-04 | Entitlement 必须表示 User 实际拥有的 resource access 或 usage rights。 | Must |
| FR-05 | Allowance 以及其他 Plan/Entitlement policy values 必须可以变化，且不得因此重写已经记录的 Usage。 | Must |

### Resource topology

| ID | Requirement | Priority |
| --- | --- | --- |
| FR-06 | domain model 必须将 Resource Pool 与具体 Node 分开表示。 | Must |
| FR-07 | 初始 Resource Pool categories 必须包含 Standard 和 Premium。 | Must |
| FR-08 | Usage attribution 必须保留具体 Node identity，并且必须支持将该 Node 解析或聚合到其相关 Resource Pool。 | Must |

### Usage capture and aggregation

| ID | Requirement | Priority |
| --- | --- | --- |
| FR-09 | 每条 Usage record 或等价的 immutable observation 至少必须保留 User、Node、time 或 reporting period，以及 traffic amount。 | Must |
| FR-10 | Usage 必须可以按 User、Resource Pool 和 Node 聚合，且不得丢失底层 attribution dimensions。 | Must |
| FR-11 | metering model 必须支持 Customer Usage 与 Infrastructure Usage 这两个不同的 observation perspectives。 | Must |
| FR-12 | Customer Billing Cycle 必须可以独立于 VPS provider resource cycle 表示。 | Must |
| FR-13 | 开始新的 Customer Billing Cycle 时，不得删除或使此前 cycles 的历史 Usage 失效。 | Must |
| FR-14 | Plan、Entitlement 或 Allowance 的变化不得修改、删除或清零已经发生的 Usage。 | Must |
| FR-15 | system 必须保留足够的 time/period context，以区分不同 cycles 中的 Usage，并支持后续 re-aggregation。 | Must |

### Operational behavior

| ID | Requirement | Priority |
| --- | --- | --- |
| FR-16 | 本 Iteration 只提供 metering 与 observability requirements；不得执行 hard quota enforcement。 | Must |
| FR-17 | metering 或 metering-observability failure 不得导致现有 proxy data plane unavailable。 | Must |
| FR-18 | 本阶段的 metering requirements 不得要求特定 implementation component；Collector、database、API 和 Portal decisions 仍在本 baseline 范围之外。 | Must |

### Documentation and secret handling

| ID | Requirement | Priority |
| --- | --- | --- |
| FR-19 | Git documentation 不得包含 credentials、UUIDs、subscription tokens、private keys、passwords 或等价的 secrets。 | Must |
| FR-20 | documentation 中未来使用的任何 example data 都必须是 synthetic 的，且不得能够作为 production access material 使用。 | Must |

## Non-Functional Requirements

| ID | Requirement | Priority |
| --- | --- | --- |
| NFR-01 | Traceability：Usage observations 必须保留足够稳定的 attribution 与 time context，以解释 aggregate 的来源。 | Must |
| NFR-02 | Historical integrity：已经记录的 Usage 必须采用 append-preserving 方式，或以其他方式避免 policy changes 重写历史事实。 | Must |
| NFR-03 | Isolation：metering failures、delayed observations 或 unavailable metering views 必须在不使现有 proxy data plane offline 的情况下处理。 | Must |
| NFR-04 | Aggregation consistency：相同的底层 observation 不得静默地产生相互矛盾的 User、Resource Pool 和 Node totals。 | Must |
| NFR-05 | Separation of concerns：customer-facing usage analysis 必须与 provider-facing infrastructure accounting 保持可区分，即使两者涉及重叠 traffic。 | Must |
| NFR-06 | Security hygiene：documentation、examples 和 review artifacts 不得包含 secret material；需要示例时必须使用 placeholders 或 synthetic identifiers。 | Must |
| NFR-07 | Reviewability：requirements、decisions 及其后续变更必须记录在 Git 中，并包含足够 context 以识别已决定事项和仍未解决事项。 | Should |

## Out of Scope

以下内容由本 Iteration 明确不实现，也不作决定：

- Collector 或 agent implementation；
- database schema、migrations 或 storage selection；
- metering API、internal service API 或 public API；
- customer Portal、login、authentication 或 account-management flows；
- subscription system 或 subscription delivery；
- load balancer（LB）design、failover 或 routing policy；
- automatic scheduling、capacity scheduling 或 provider automation；
- hard quota enforcement、blocking、throttling 或 automatic downgrade；
- invoice generation、payment processing、upgrade pricing、proration 或 billing settlement；
- 对现有 proxy data-plane configuration 的修改；
- 连接、检查或修改任何 VPS；
- 将真实 credentials、UUIDs、subscription tokens、private keys、passwords 或类似 secrets 记录到 Git。

## Acceptance Criteria

当以下所有 criteria 都可以从 repository documentation 与后续 implementation plan 中得到验证时，本 baseline 才具备 review 条件：

| ID | Acceptance criterion |
| --- | --- |
| AC-01 | User、Plan、Subscription、Credential、Entitlement、Allowance、Resource Pool、Node 和 Usage 均已定义，并且各自 responsibilities 不同。 |
| AC-02 | Free、Basic 和 Plus 被标记为初始 Plan values，同时明确 Plan 不得被当作 Entitlement。 |
| AC-03 | Standard 和 Premium 被标记为初始 Resource Pool categories，同时明确 Resource Pool 与 Node 分离。 |
| AC-04 | 最低 Usage traceability dimensions 包含 User、Node、time/period 和 traffic amount；同时明确要求按 User、Resource Pool 和 Node aggregation。 |
| AC-05 | Customer Usage 与 Infrastructure Usage 被描述为不同的 observation perspectives，并明确 customer billing cycles 独立于 provider resource cycles。 |
| AC-06 | 明确历史 Usage 在新的 billing cycles 开始后，以及 Plan、Entitlement 或 Allowance 变化后都必须保留。 |
| AC-07 | 明确本 Iteration 仅限 Metering/Observability，并明确排除 hard quota enforcement。 |
| AC-08 | metering failure 与现有 proxy data plane 的 isolation 同时作为 functional 和 non-functional requirement 被说明。 |
| AC-09 | 本 Iteration 不包含 Collector、database、API、Portal、login、subscription、LB、automatic scheduling 或 VPS modification 的 implementation。 |
| AC-10 | 文档不包含 production secrets，且每个未解决的 product/operational question 都明确标记为 undecided。 |

## Open Questions — Undecided

以下问题仍然明确未裁决。它们需要 Product Owner 和/或后续 architecture/operations decisions；本 requirements baseline 不回答这些问题：

1. Standard 与 Premium resources 的 numeric quota 或 allowance values 是什么？它们如何与 Free、Basic 和 Plus 关联？
2. Plan 与 Entitlement 变化时，upgrade、downgrade、proration、renewal 和 effective-time rules 是什么？
3. 当 Node 变更 Resource Pool、被替换，或其 membership interval 跨越 reporting period 时，应如何进行 Usage attribution？
4. VPS provider resource cycle 的 authoritative source 和 acquisition method 是什么？provider data 延迟或 unavailable 时如何处理？
5. AnyTLS 的 precise metering approach 是什么，包括 observable byte boundary、direction handling、aggregation granularity 和已知 limitations？
6. canonical customer billing-cycle timezone、start rule、end rule 和 reporting granularity 是什么？
7. traffic units、rounding rules、counter-reset rules，以及 duplicate/out-of-order observations 的处理规则是什么？
8. detailed Usage records 与 derived aggregates 适用什么 retention、correction、audit 和 privacy policies？
9. User creation、merge、deactivation 以及 legal/data-retention requests 适用什么 identity lifecycle rules？
10. metering observability 适用什么 availability、delay、reconciliation 和 recovery targets，同时保证 proxy data plane 保持独立？

在这些问题作出决定前，任何 implementation 或 documentation 都不得将其答案表述为已确立的 product policy。
