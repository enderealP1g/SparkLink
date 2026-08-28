# ADR-0002: Project boundaries and Agentic Operations

| Field | Value |
| --- | --- |
| Status | Accepted — Product Owner decision, 2026-08-28 |
| Scope | Project-level responsibility and terminology boundaries |

## Decision

- `Deployer != Control Plane`。`Deployer` 负责单 VPS inspect、deploy、configure、verify、upgrade、repair、rollback、local inventory 与 adoption planning；未来 `Control Plane` 负责 durable project/fleet facts、policy、relationships 与 operation intent。
- `deployer-ready != Control-Plane-managed`。`recognized host`、`known deployment layout`、`sparklink-deployed` 与 `deployer-ready` 只表示 Deployer-owned local evidence，不表示 registration、entitlement、fleet governance 或 production acceptance。
- `node-descriptor.json` 是 Deployer artifact，不是未来 Control Plane Node schema 或 registration record。
- Codex/codexop 是高权限 `Agentic Operator`，负责 reasoning、approved execution、verification 与 cross-system investigation；不是 durable project source of truth。
- `Operation` 是 future project-level domain concept，表示 operational intent/transaction context，不等于 shell command。本 Iteration 不实现 Operation subsystem。

## Consequences

Deployer 的 inventory、descriptor、adoption plan 与 command behavior 保持 Deployer-local。任何未来 registration、fleet operation 或 durable state 都必须经过独立的 Control Plane contract；agent transcript 不能替代该 source of truth。

本 ADR 不定义 Control Plane schema、API、orchestration engine、权限模型或 Operation implementation。

## Source references

本 ADR promotion 自 `sparklink-deployer` PR3 当前提交中的 [SYSTEM_MAP](https://github.com/enderealP1g/sparklink-deployer/blob/d6b9f0dc626aa5ea67cf96c24ec96ee2e06cb3ee/docs/product/SYSTEM_MAP.md)、[RESPONSIBILITY_MAP](https://github.com/enderealP1g/sparklink-deployer/blob/d6b9f0dc626aa5ea67cf96c24ec96ee2e06cb3ee/docs/product/RESPONSIBILITY_MAP.md)、[ADR-0003](https://github.com/enderealP1g/sparklink-deployer/blob/d6b9f0dc626aa5ea67cf96c24ec96ee2e06cb3ee/docs/adr/0003-deployer-ready-not-control-plane-managed.md)、[ADR-0007](https://github.com/enderealP1g/sparklink-deployer/blob/d6b9f0dc626aa5ea67cf96c24ec96ee2e06cb3ee/docs/adr/0007-codex-agentic-operator.md) 以及 [descriptor.py](https://github.com/enderealP1g/sparklink-deployer/blob/d6b9f0dc626aa5ea67cf96c24ec96ee2e06cb3ee/src/sparklink_deployer/descriptor.py)。
