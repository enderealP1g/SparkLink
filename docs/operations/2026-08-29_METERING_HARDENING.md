# 2026-08-29 Metering Hardening Record

| Field | Value |
| --- | --- |
| Status | Automatic collector deployed and observed healthy — 2026-08-29 |
| Scope | Xray counter lifecycle、Usage ledger、coverage freshness 与 automatic collection |
| Related decisions | [`ADR-0003`](../decisions/0003-iteration-01-runtime-and-metering-boundary.md)、[`ADR-0004`](../decisions/0004-production-mvp-vertical-slice.md)、[`ADR-0005`](../decisions/0005-metering-hardening-and-automatic-collection.md) |

本文件记录 Production Hardening 的 point-in-time implementation/evidence，不代表永久 current state。它不改变现有 proxy data plane，也不把当前 Candidate Architecture Proposal 升级为 To-Be Architecture。

## Automatic collection boundary

```text
Windows protected control plane
  OS supervisor → Python interval collector
       ├─ SSH read-only → RackNerd Xray Stats API
       ├─ SSH read-only → VMISS Xray Stats API
       └─ SSH read-only → hypro02 Xray Stats API
                              ↓ HTTPS + admin authentication
                      QQG loopback Control Plane + SQLite
```

Automatic collector 只读取 Xray per-user counters，哈希 runtime identity 后发送 observation。SSH private keys 与 Control Plane admin secret 只存在 protected runtime locations，不写入 Git；Windows admin secret 使用 `LocalMachine` DPAPI 并由 ACL 限定读取者。QQG 不接收跨 Node collection key。Windows `Task Scheduler` 只作为 OS process supervisor，不执行产品 scheduling、quota 或 billing 规则。

One-shot collector 仍可通过同一 config/credential boundary 运行，作为 manual operations fallback。若一个 Node 失败，其他 Node 继续采集；失败会写入 `coverage gap`，不会生成 synthetic zero。

## Correctness semantics

- 每条 observation 保留 `Node`、hashed runtime identity、source、`counter_epoch`、observation time 与 raw uplink/downlink counters。
- 相同 observation 重放为 idempotent duplicate；同一 idempotency/natural key 携带不同 counter 则返回 conflict，不静默覆盖历史。
- same-epoch counter decrease 记录为 `counter_reset_or_non_monotonic`，不产生负 delta；process epoch 变化不删除已有 Usage。
- late/out-of-order observation 保留 raw record，但不重新计算已经落账的后续 delta，避免重放导致 double count；该不完整性通过 evidence/coverage 继续暴露。
- `available` coverage 超过默认 `900` 秒会解释为 `stale`。User view 在 coverage、mapping 或 cycle 不足时显示 `Unknown`/`NULL`，不把不可观测 Usage 显示为 `0`。
- Customer Usage 只使用完成 `Credential → User → Node → Resource Pool → Customer Billing Cycle` attribution 的 ledger；Node-level `Infrastructure Usage` 单独聚合。

## Verification evidence

| Check | Result |
| --- | --- |
| Local unit tests | `17 tests` passed；包含 reset、duplicate/conflict、out-of-order、partial failure、empty source、stale coverage 与 timestamp validation |
| Python compile check | `src` 与 `tests` `compileall` passed |
| AnyTLS investigation | isolated `sing-box 1.13.16` evidence complete；installed binary 缺少 V2Ray API，结论为 `Deferred pending reliable metering` |
| Automatic collector | Windows `Task Scheduler` task 使用 protected repository runtime secret；重注册后连续两个 interval 均记录 3/3 Nodes ingest、`failed=0`，Task 保持 `Running` |
| Production data plane boundary | hardening code 不修改 Xray/Nginx/WireProxy config 或 proxy listeners；QQG Control Plane 更新保留 pre-change backup，health、service 和 listener retest 通过 |

## Fallback and rollback

- collector process 失败时，保留既有 append-preserving ledger；Admin/User view 使用 freshness/coverage state。
- automatic path 不可用时，运行 one-shot collector；不要以手工填入 counter、host total、Nginx total 或 AnyTLS total 替代 per-user evidence。
- Control Plane code rollback 前必须保留当前 file backup，恢复原 owner/group/mode，执行 health check，并确认 Xray/Nginx/WireProxy listeners 未受影响。SQLite historical Usage 不通过代码 rollback 删除。
- Windows supervisor rollback 为停止并 unregister `SparkLink-Metering-Collector`；protected DPAPI secret 位于 ignored `runtime` location，可独立移除，不能提交到 repository。

## Remaining gaps

- DediRock 的 Xray Stats API 仍是 verified coverage gap；当前 automatic collector 不伪造其 User Usage。
- x-ui persistent `client_traffics` 尚未作为 automatic source reconciliation authority；不能与 Stats API 直接相加。
- AnyTLS 的 custom build per-user counter 仍受 package provenance、destructive reset、restart loss 与 durable mapping 约束，不进入 production Subscription。
- Windows OS supervisor 的实际运行依赖 protected admin secret、现有 SSH aliases/keys 与 host login/session availability；若这些条件变化，必须重新验证 collection freshness。
- 本次自动任务早期曾因旧 `AppData` secret path 不可被 Task Scheduler context 读取而失败；改用 repository ignored runtime path 后，DPAPI 解密与连续 collection cycle 均通过。历史 log 中的失败记录不代表当前 cycle 状态。
