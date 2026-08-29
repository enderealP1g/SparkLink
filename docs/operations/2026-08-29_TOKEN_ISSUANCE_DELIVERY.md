# 2026-08-29 Admin token issuance and protected delivery

| Field | Value |
| --- | --- |
| Status | Implemented and live-verified |
| Scope | Production Operations correctness: Admin credential issuance, rotation, delivery and rejection verification |
| Public surfaces | `https://spark.enrpiglink.top` Portal; `https://sub.enrpiglink.top` Subscription base |
| Operator surface | Windows control machine through the existing SSH tunnel to QQG loopback Control Plane |

本记录只描述 workflow、结构和脱敏 evidence，不包含任何 token、完整 subscription URI 或其他 plaintext credential。

## Security contract

- Control Plane SQLite `users` 只保存 `portal_token_hash` 与 `subscription_token_hash`；legacy `subscription_token` 列已迁移并移除。
- Portal token 与 Subscription token 是两种独立 credential。Portal 只用于 Bearer `/api/me`；Subscription 只用于 `X-SparkLink-Subscription-Token`、`/subscription` 或带 token 的 `/u/<token>` delivery path。
- Admin issuance 原子替换所选 hash；旧 token 随 hash replacement 立即失效。`revoke_old=false` 不被接受。
- Plaintext 只在本次 issuance 的受保护响应到 Windows operator memory 和 one-time ignored delivery bundle 中存在；不写入 Git、日志、operations docs 或聊天。
- Windows delivery 位于 ignored `runtime\delivery`，文件以当前 operator ACL 保护。Admin secret 继续从 ignored LocalMachine DPAPI path 读取，不进入 CLI 参数或日志。
- 遗失 plaintext 不尝试恢复；没有 legacy plaintext 可供一次性 transition preservation 时，operator 只返回 rotate-required failure。

## Operator workflow

从 repository root 执行；默认通过 SSH tunnel 访问 `127.0.0.1:8080`，不把 Control Plane Admin endpoint 暴露为 operator 的公网依赖。

```powershell
python deploy\issue_user_tokens.py list
python deploy\issue_user_tokens.py issue --user-id <USER_ID> --token-kind portal
python deploy\issue_user_tokens.py issue --user-id <USER_ID> --token-kind subscription
python deploy\issue_user_tokens.py issue --user-id <USER_ID> --token-kind both
python deploy\issue_user_tokens.py verify --bundle runtime\delivery\<BUNDLE>.json
python deploy\issue_user_tokens.py copy --bundle runtime\delivery\<BUNDLE>.json --kind portal
```

`issue` 始终要求 `revoke_old=true`，把本次选择的 plaintext 写入受保护 bundle，并默认执行 new/wrong/cross-kind verification。需要验证已知旧 token 时，显式传入上一份 bundle，并使用 `--consume-old-bundle`；该临时 bundle 会在 old-token rejection 成功后删除。`copy` 是显式 local-only clipboard helper，不会自动复制或向聊天输出 secret。

Admin API 仅提供非 secret user metadata list，以及 `POST /api/admin/token-issuance`。API 的 issuance response 是唯一的 plaintext handoff；operator stdout 只输出 user id、bundle path、verification result 和固定安全状态。

## Legacy transition rule

对仍有旧 `subscription_token` 列的数据库，只有在迁移前明确执行 `legacy-export --allow-legacy-plaintext-export` 才允许为指定 User 保存一次性受保护 URL。它不 rotate 该 Subscription token。Control Plane migration 随后 hash 该值、重建 `users` table、移除 plaintext 列、checkpoint/VACUUM；完成 acceptance 后删除 legacy DB rollback copies。对已经丢失的 plaintext，不执行该路径。

## Live acceptance evidence

- Control Plane service active，loopback `/healthz` 返回 `ok`。
- live `users` schema 只含两类 token hash；6/6 User 的 hash 形状有效；foreign-key check clean；users、cycles、credentials、subscription entries、observations、ledger 计数在迁移前后保持不变。
- root identity 为 `usr_plus_manual_01` / `root`，Plan `Plus`，role `OWNER`，status `active`。
- root Portal new token 被接受；本轮 superseded Portal token、随机 wrong Portal token、Portal-as-Subscription 均被拒绝。
- root 当前 Subscription URL 未被 rotate，迁移后仍被接受；随机 wrong Subscription token、Subscription-as-Portal 均被拒绝。
- Portal 页面已完成真实登录；安全 `/api/me` acceptance 检查确认 root self-scope、`Plus`、`OWNER`、`legacy-pre-baseline` Customer Cycle（`Asia/Shanghai`）以及恰好独立的 `STANDARD`/`PREMIUM` pools。
- Admin metadata list 实际返回 6 个 User。除 root Portal token 的本次 issuance/verification 外，没有自动 rotate 其它 User 的现有 token。

## Rollback and retention

迁移前 DB snapshot 曾仅在变更窗口内保留，并在 acceptance 后删除；当前保留的 post-migration DB snapshot 是 hash-only，旧 code snapshot 不含 User data。若后续需要回滚，先保留 append-preserving Usage ledger，恢复 code 后重新运行 schema/health/Portal/Subscription rejection checks；禁止恢复任何含 plaintext token 的 DB backup。
