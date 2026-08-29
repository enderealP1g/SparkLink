# 2026-08-29 Production identity and cycle reconciliation record

| Field | Value |
| --- | --- |
| Status | Point-in-time implementation/evidence record |
| Scope | stable `User` identity、`Credential` mapping、Subscription projection 与 cycle metadata |
| Evidence date | 2026-08-29 |
| Related decisions | [`ADR-0001`](../decisions/0001-project-domain-identity-and-serving-relationships.md)、[`ADR-0004`](../decisions/0004-production-mvp-vertical-slice.md)、[`ADR-0005`](../decisions/0005-metering-hardening-and-automatic-collection.md)、[`ADR-0006`](../decisions/0006-production-identity-and-customer-cycle.md) |

本文件记录一次 production reconciliation snapshot，不代表永久 current state。它不记录 UUID、credential、subscription token、private key、password、完整 URI 或其他 secret/private runtime material；runtime token bundle、SQLite database 与 Node backup 只存在受保护的 runtime location。

## Identity reconciliation

- SparkLink 已建立自己的 stable `User` identity；当前六个真实业务主体分别保留既有 stable user ID 或获得新的 `usr_*` identity。
- 当前 Plan/role reconciliation 为：一个 `OWNER`/`Plus`、两个 `Plus` customer、一个 `Basic` customer、两个 `Free` customer。Allowance 数值、upgrade pricing/proration 与 automatic effective time 仍未裁决。
- RackNerd、VMISS、`hypro02` 的 Xray/VLESS runtime identity 已建立 `Credential → User` mapping。legacy credentials 保留用于历史归因，managed credentials 用于后续 projection；旧 credentials 未因本次迁移被静默删除或撤销。
- 本次 live database reconciliation 后，credential kind 为 `legacy=8`、`managed=20`；既有 observation/ledger history 未被重建或清空。

## Node and Infrastructure Resource boundary

| Infrastructure Resource | SparkLink Node | Pool/status at snapshot | 说明 |
| --- | --- | --- | --- |
| `qqgnet-la-9929` | `hypro02` | `PREMIUM / CONDITIONAL` | 允许当前小规模 Plus service 使用；不等同于 fully-qualified Premium |
| `vmiss-la-9929` | `vmiss` | `PREMIUM / verified` | existing Premium serving Node |
| `racknerd-ny-bf` | `racknerd` | `STANDARD / verified` | existing Standard serving Node |
| `dedirock-la-bf` | `dedirock` | `reference-only / unqualified` | 不进入当前正式 user-facing subscription surface |

同一 Infrastructure Resource 的 IP、OS 或 runtime update 不改变 Node identity；替换为另一份 Provider resource 才创建新的 Node。当前 snapshot 没有执行 Node replacement。

## Independent cycles

- `Customer Billing Cycle` 使用 `Asia/Shanghai` timezone，policy 为每月 15 日 00:00 至次月 15 日 00:00；`2026-09-15 00:00 Asia/Shanghai` 是本次 policy baseline。
- baseline 之前的 Usage 只作为 `legacy/pre-baseline` history 保留，不删除、不清零、不重写，也不因新 cycle 生成而迁移成新的 commercial Usage。
- 四个 VPS instance 的 host OS timezone 以 read-only discovery 记录为 `Etc/UTC`。这是 `Provider Resource Cycle` 的已验证 local-time metadata，不是用户 cycle timezone。
- Provider contract/financial cadence 与 traffic reset authority 分开保存。当前 provider traffic reset、authoritative usage export 与 cycle acquisition 仍是 `Unknown`；不能从 location、Next Due 或套餐文字推断 reset。

## Subscription and service surface

- `Subscription` 使用独立于 Portal access 的 `Subscription token`，只是由当前 User、Entitlement、Pool/Node membership 与 Credential mapping 生成的 projection，不是 Usage attribution source of truth。
- 当前正式 projection 只使用已经完成 per-user attribution 的 Xray/VLESS paths。AnyTLS 仍为 installed/standby capability，状态为 `Deferred pending reliable metering`，没有进入 production Subscription。
- `sub.enrpiglink.top` 仍是 delivery boundary。public `/u/<subscription-token>` 请求由 Worker 转换为内部 `X-SparkLink-Subscription-Token` header，Control Plane 不再把 Subscription token 当作 Portal token 解释。

## Live metering evidence after reconciliation

- QQG `hypro02` 的 isolated loopback VLESS transfer 使用真实 managed User identity；两次 collection 形成 `baseline + delta`，该 User 的 `PREMIUM` ledger 增加 `4,414` bytes。该数值仅是 test traffic evidence，不是 allowance 或 quota。
- 当前 Windows automatic collector 使用 protected runtime secret 与 SSH read-only pull；连续 interval summary 为 `1 ingested`（`hypro02`）、`2 unknown`（RackNerd/VMISS StatsService 可达但当前没有 per-user counter rows）、`0 failed`。
- `Unknown` 表示当前 observation coverage 不足，不表示 zero。RackNerd/VMISS 的 existing Xray Stats API capability 仍存在；identity migration 后没有新的 per-user traffic counter 不能作为“无 traffic”或“用户不存在”的证据。
- Collector 只向 Control Plane 发送 hashed runtime identity、counter、`counter_epoch`、observation time 与 source；Control Plane 以 append-preserving raw observation/ledger 保存历史，并按 User、Node、Pool、Customer Billing Cycle 生成视图。`Provider Resource Cycle` 不替代 Customer Cycle。

## Runtime safety and rollback points

- Node-side identity migration 在 RackNerd、VMISS、`hypro02` 分别保留受保护 backup directory；Control Plane migration 另保留 pre-change backup。backup 内容不纳入 Git。
- migration 后 Xray config test、service recovery、loopback Stats API 与 existing listeners 通过；本次 reconciliation 未修改 DediRock、既有 Node 的非 identity proxy behavior、provider configuration 或 production DNS。
- 回滚时先停止对应 management/migration operation，恢复目标 Node 的原 config 与 x-ui database backup，恢复原 owner/group/mode，执行 config test、service check 与 listener check；不得通过删除 SQLite rows 回滚历史 Usage。
- Windows automatic collector 的 rollback 是停止/重新注册 `SparkLink-Metering-Collector`；one-shot collector 仍可作为 fallback。startup cleanup 只匹配该 task 的固定 SSH forward，非匹配 SSH listener 不会被清理。

## Remaining unknowns

- numeric allowance、upgrade/proration、provider cycle acquisition 与 traffic reset authority 仍需 Product Owner/Operations evidence。
- RackNerd/VMISS 当前 collection source 可达但在本 snapshot 没有 per-user rows；需要未来真实用户 traffic 或受控 acceptance traffic 才能恢复其 current counter coverage。
- DediRock Xray Stats API gap、AnyTLS reliable per-user accounting、长期 `PREMIUM / CONDITIONAL` qualification 继续保持既有 Deferred/Operations 状态。
