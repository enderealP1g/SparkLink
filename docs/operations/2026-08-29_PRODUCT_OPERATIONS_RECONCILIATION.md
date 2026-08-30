# 2026-08-29 Product / Operations intent reconciliation

本记录描述 P0→P6 的最小 operations vertical slice。它不引入新的 Plan，不启用 P7 hard quota/automatic blocking，也不把 provider capacity 或 host total 伪装成 User Usage。

## Product model

| Plan | Default entitlement |
| --- | --- |
| Free | 无 Standard / Advanced / Premium projection |
| Basic | Standard + Advanced |
| Plus | Standard + Advanced + Premium |

`ADVANCED` 是一等 Pool；Node access、Subscription capability、metering coverage 与 quota policy 是彼此独立的维度。当前唯一允许的 runtime protocol 是 Xray/VLESS；AnyTLS 保持 Deferred。

Premium allocation 使用通用 time-effective override，而不是 username 分支：Hegin 为 VMISS Primary / QQG Available，root 为 QQG Primary / VMISS Reserved，abing 为 VMISS Deny / QQG Primary。abing 的 QQG 200 GB 与四名 Advanced User 的 DediRock 700 GB allowance 仅是 `policy_only` metadata；P7 hard enforcement 未授权。

Customer Cycle 固定为 `Asia/Shanghai` 每月 15 日到下月 15 日，并保留 baseline 前 legacy window。Provider Resource Cycle 单独记录；reset timestamp 只有 provider authoritative snapshot/API/dashboard evidence 才能写入。

## P0–P6 implementation

- P0：collector 通过现有受保护 Windows task/SSH tunnel 运行；每个 interval 写 coverage 与 heartbeat。失败、无 per-user counter、stale 都显示为 gap/unknown，而非 0。
- P1：Control Plane schema 支持 Advanced pool、Node capability/admission、VLESS-only subscription projection，以及 DediRock 的 staged admission boundary。DediRock runtime 没有在本 checkpoint 重启或改写。
- P2：`user_access_overrides` 与 `operational_budgets` 是 append-preserving、time-effective、可审计的 management-plane records。`budget_kind=enforceable` 被 API 明确拒绝。
- P3：Portal/Admin 返回 User × Node × Pool × Customer Cycle 的 Usage 与 freshness；Provider cycle 另存于 ledger linkage，不能替代 Customer Cycle。
- P4：`python deploy\admin_console.py` 启动 loopback-only OWNER Console。页面只显示 safe metadata；通过本机 operator bundle 的明确 copy 动作取得当前 Portal credential 或个人 Subscription URL。
- P5：Control Plane 只保存 token hash；operator bundle/DPAPI secret 位于 ignored、ACL-protected `runtime\`。migration event 支持 issued/delivered/fetched/managed_traffic_observed/confirmed/legacy_retirement_ready/retired；legacy revoke 需要精确 confirmation 与 latest current-token confirmation。
- P6：`python deploy\record_provider_snapshot.py --file <non-secret-snapshot.json>` 导入带 source/observed_at/status 的 provider snapshot。无 authoritative evidence 时使用 `status=unknown`；导入器不接触 runtime credential。

## Operator acceptance

在 repository root 执行：

```powershell
python deploy\admin_console.py
```

然后在本机打开默认 loopback URL。User detail、effective access、coverage age、migration state 与 provider snapshot 均不会显示 token、Subscription URL、runtime URI、UUID 或 hash。要发送材料时，在目标 User 行明确点击 `Copy Portal` 或 `Copy Subscription URL`；只有本机 clipboard 被更新，response 不含 secret。

CLI fallback：

```powershell
python deploy\issue_user_tokens.py list
python deploy\issue_user_tokens.py copy --user Hegin --kind portal
python deploy\issue_user_tokens.py copy --user Hegin --kind subscription
```

六个 User 的 bundle 使用稳定路径 `runtime\delivery\<username>\delivery.json`，OWNER index 只包含 User/Plan/path/status/migration metadata。缺失 plaintext 只能 rotate/issue；现有可信 bundle 则复用。任何六用户 reconcile 都不自动撤销仍可能使用的 legacy/shared Subscription。

## Evidence and gate

本 checkpoint 的 local regression、schema migration 与 safe metadata API 是 management-plane evidence。DediRock live inspection 仍显示：服务可用，但当前没有可用于 SparkLink per-user attribution 的 Stats API/listener；为加入四名 Advanced User 需要 runtime identity/config mutation，且 systemd unit 没有可用的 no-interruption reload contract。该动作可能中断现有 data plane，因此保留为下一维护窗口的显式 gate。

在 gate 解除前，DediRock 继续为 `reference-only/unqualified`，其 Usage 为 `Unknown`，不会进入 current Subscription。QQG/VMISS 的既有 Premium paths、legacy/shared access 与历史 Usage 不因本模型变更而撤销或重写。
