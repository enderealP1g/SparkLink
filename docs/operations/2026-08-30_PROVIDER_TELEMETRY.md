# 2026-08-30 Provider telemetry integration checkpoint

本 checkpoint 建立四家 Infrastructure Resource 的 provider telemetry 管道。它只更新 Control Plane management metadata，不读取或修改 Xray/Nginx/WireProxy/sing-box runtime，也不参与 proxy forwarding。

## Source contract

Provider adapter 的 source priority 是：

1. provider official API；
2. provider documented/stable endpoint；
3. operator 从 provider dashboard 导出的非 secret JSON。

`config/sparklink.provider-telemetry.example.json` 只描述 schema 和字段形状，不保存 provider login、API key、cookie、password 或其他敏感凭据。当前 operator context 没有已授权的 provider telemetry source，因此四家 adapter 都返回 source-labelled `unknown`。如果未来接入 provider account 需要新的敏感凭据、验证码或 2FA，应暂停并由 Product Owner 另行授权。

## Operator command

从 repository root 执行：

```powershell
python deploy\collect_provider_snapshots.py --dry-run
python deploy\collect_provider_snapshots.py
```

默认命令读取 private Control Plane 的现有 resource inventory，并为每个 resource 追加一条 snapshot。没有 source file 时，snapshot 的 `status` 为 `unknown`，capacity/used/remaining/reset/next due 全部保持 null；`infrastructure_resources.transfer_limit` 和 `next_due_local` 是 inventory/contract metadata，不被当作当前 telemetry。一次 trusted non-secret provider export 可通过 `--file <path>` 传入；缺少的 resource 仍会生成明确的 `unknown` snapshot。

输入文件只能包含 normalized provider telemetry 字段。工具拒绝额外字段、secret-like credential fields、`unknown` 携带 bytes/cycle 值、`available` 缺少完整 byte counters，以及不一致的 `capacity = used + remaining`。工具输出只包含 counts/status，永不回显输入内容或 Control Plane admin secret。

## Live evidence

本次 live run 先通过 `--dry-run` 解析到 4 个 resource，随后追加 4 条 snapshot，结果为 `4 recorded / 0 failed`：

| Provider | Resource | Snapshot | Current values |
| --- | --- | --- | --- |
| DediRock | `dedirock-la-bf` | `unknown` | all provider telemetry values `Unknown` |
| QQGNet | `qqgnet-la-9929` | `unknown` | all provider telemetry values `Unknown` |
| RackNerd | `racknerd-ny-bf` | `unknown` | all provider telemetry values `Unknown` |
| VMISS | `vmiss-la-9929` | `unknown` | all provider telemetry values `Unknown` |

Admin Infrastructure view reads the latest local Control Plane snapshot and displays provider, resource, freshness, source, observed time, and unknown reset/remaining state. It does not synchronously call providers when OWNER opens the page. The current collector heartbeat remains independent: 4 Nodes attempted, 3 per-user Xray sources ingested, DediRock 1 coverage gap/unknown, 0 failed.

The final Control Plane validation was deployed with a protected backup at `/var/backups/sparklink-control-plane/provider-telemetry-20260830T051442Z`. The deployed source hash matched the tested local candidate; the process health endpoint returned `200`, the source file retained `sparklink:sparklink 0640`, and Xray/Nginx/WireProxy remained active.

## Boundary and rollback

Provider totals are for infrastructure visibility and future allocation review only. They are never copied into Customer Usage, never used to fill a per-user zero, and never enable the 200GB/700GB policy as a hard quota. Customer Cycle and Provider Resource Cycle remain separate. A provider adapter failure only produces an operator error or an `unavailable/unknown` snapshot; it cannot alter the proxy data plane. Snapshot records are append-only management evidence; a later correction appends a newer source-labelled record rather than deleting historical evidence.
