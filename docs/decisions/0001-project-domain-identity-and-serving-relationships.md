# ADR-0001: Project domain identity and serving relationships

| Field | Value |
| --- | --- |
| Status | Accepted — Product Owner decision, 2026-08-28 |
| Scope | Project-level domain semantics；不定义 schema、API、storage 或 implementation |
| Related requirements | `FR-01`、`FR-03`、`FR-04`、`FR-06`、`FR-08`、`FR-09`、`FR-10`、`FR-14` |

## Decision

### Infrastructure Resource and Node

- `Infrastructure Resource` 表示由 Provider 购买并管理的资源。
- `Node` 表示 SparkLink operational identity，并分配给某个 Infrastructure Resource。
- 同一 Infrastructure Resource 的换 IP、OS reinstall 或 runtime update 不创建新的 Node。
- 替换为另一份 Provider resource 时创建新的 Node；旧 Node 进入 `Retired`，其 Pool membership 与 history 保留。

### Serving relationship

项目级 conceptual relationship 为：

```text
Plan → Entitlement → Resource Pool → serving Node(s)
```

这不是 `Plan → Node` 的直接绑定，也不表示 Deployer inventory 可以创建 Entitlement 或 Node registry relationship。

### Identity and delivery

- `Credential != User`。runtime technical identity 应通过 Credential/User mapping 归因到 stable User。
- `Subscription` 是由已确定的 Entitlement、Resource Pool、serving Node(s) 和 Credentials 生成的 projection，不是 source of truth。
- `Subscription` 不作为 Usage attribution authority。

## Consequences

Usage attribution 必须能够区分 Infrastructure Resource、Node、Pool membership 与 Credential/User mapping 的时间关系。Node replacement 不得被表示为同一 Node 的静默重写，也不得删除既有 history。

本 ADR 不决定 numeric quota、pricing、billing policy、具体 identity store 或 Subscription implementation。

## Source references

本 ADR promotion 自 `sparklink-deployer` PR3 当前提交中的 [DOMAIN_MODEL](https://github.com/enderealP1g/sparklink-deployer/blob/d6b9f0dc626aa5ea67cf96c24ec96ee2e06cb3ee/docs/product/DOMAIN_MODEL.md)、[ADR-0002](https://github.com/enderealP1g/sparklink-deployer/blob/d6b9f0dc626aa5ea67cf96c24ec96ee2e06cb3ee/docs/adr/0002-plan-entitlement-pool-node.md)、[ADR-0004](https://github.com/enderealP1g/sparklink-deployer/blob/d6b9f0dc626aa5ea67cf96c24ec96ee2e06cb3ee/docs/adr/0004-subscription-is-projection.md) 与 [ADR-0006](https://github.com/enderealP1g/sparklink-deployer/blob/d6b9f0dc626aa5ea67cf96c24ec96ee2e06cb3ee/docs/adr/0006-infrastructure-resource-vs-node.md)。这些 source docs 的具体 implementation/schema 仍由 Deployer repository 管理。
