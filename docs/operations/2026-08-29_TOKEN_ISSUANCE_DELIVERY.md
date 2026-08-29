# 2026-08-29 Admin token issuance and protected delivery

| Field | Value |
| --- | --- |
| Status | Implemented and live-verified |
| Scope | Production Operations correctness: Admin credential issuance, rotation, delivery and rejection verification |
| Public surfaces | `https://spark.enrpiglink.top` Portal; `https://sub.enrpiglink.top` Subscription base |
| Operator surface | Windows control machine through the existing SSH tunnel to QQG loopback Control Plane |

本记录只描述 workflow、结构和脱敏 evidence，不包含任何 token、完整 subscription URI 或其他 plaintext credential。

## Security contract

- Control Plane SQLite `users` 只保存 `portal_token_hash`、`subscription_token_hash`，以及可选的 hash-only `subscription_token_legacy_hash` grace slot；legacy plaintext `subscription_token` 列已迁移并移除。
- Portal token 与 Subscription token 是两种独立 credential。Portal 只用于 Bearer `/api/me`；Subscription 只用于 `X-SparkLink-Subscription-Token`、`/subscription` 或带 token 的 `/u/<token>` delivery path。
- Admin issuance 原子替换所选 hash；旧 token 随 hash replacement 立即失效。`revoke_old=false` 不被接受。
- Plaintext 只在 issuance 的受保护响应、可信 local bundle re-home 或新的 ignored delivery bundle 中存在；不写入 Git、日志、operations docs 或聊天。
- Windows delivery 位于 ignored `runtime\delivery`，文件以当前 operator ACL 保护。Admin secret 继续从 ignored LocalMachine DPAPI path 读取，不进入 CLI 参数或日志。
- 遗失 plaintext 不尝试恢复；没有 legacy plaintext 可供一次性 transition preservation 时，operator 只返回 rotate-required failure。

## Operator workflow

从 repository root 执行；默认通过 SSH tunnel 访问 `127.0.0.1:8080`，不把 Control Plane Admin endpoint 暴露为 operator 的公网依赖。

```powershell
python deploy\issue_user_tokens.py list
python deploy\issue_user_tokens.py issue --user-id <USER_ID> --token-kind portal
python deploy\issue_user_tokens.py issue --user-id <USER_ID> --token-kind subscription
python deploy\issue_user_tokens.py issue --user-id <USER_ID> --token-kind both
python deploy\issue_user_tokens.py reconcile
python deploy\issue_user_tokens.py verify --bundle runtime\delivery\<BUNDLE>.json
python deploy\issue_user_tokens.py copy --user Hegin --kind subscription
python deploy\issue_user_tokens.py copy --user Hegin --kind portal
python deploy\issue_user_tokens.py revoke-legacy --user-id <USER_ID>
```

`reconcile` 是六用户交付闭环：它只接受当前的 `root`、`Hegin`、`abing`、`dangbin`、`liuwen`、`zhanhao` 六个 active User，输出 `runtime\delivery\<username>\delivery.json` 与 OWNER-only `OWNER-DELIVERY-INDEX.json`。已有可信 bundle 会复用并规范化；plaintext 已不可恢复的 User 才会 issue。对这次五个缺失 bundle 的 User，Portal 旧 hash 立即替换，Subscription 新 hash 生效但旧 Subscription hash 保留为 grace，直到显式 `revoke-legacy`；因此 reconcile 不会为了生成交付材料无条件撤销旧 shared/legacy Subscription。

每个新 bundle 写入后都会做 local Control Plane new/wrong/cross-kind checks，并直接 GET 公网 `sub.enrpiglink.top/u/<token>`，验证 HTTP projection、计划对应的 pool/count/protocol；Free 当前无 entitlement 时预期为 `not_configured`，而不是伪造空的 available subscription。`copy --user <username> --kind portal` 复制 Portal token；`copy --user <username> --kind subscription` 复制完整 Subscription URL。两者都只在显式执行时写入本机 clipboard，stdout 不输出 secret。

`issue` 默认 `revoke_old=true`，把本次选择的 plaintext 写入受保护 bundle，并执行 new/wrong/cross-kind verification。需要 staged Subscription migration 时使用 `--retain-old-subscription`；旧 token rejection 不能与该保留模式混用。需要验证已知旧 token 时，显式传入上一份 bundle，并使用 `--consume-old-bundle`；该临时 bundle 会在 old-token rejection 成功后删除。`revoke-legacy` 只清除数据库中的 retained Subscription hash，不会生成或回显任何 plaintext。

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
- Admin metadata list 实际返回 6 个 User。首轮 root Portal issuance/verification 没有 rotate 其它 User；后续六用户 reconcile 只对没有可信 plaintext bundle 的 Hegin、abing、dangbin、liuwen、zhanhao issue 新 credential，并保留各自旧 Subscription grace hash。
- 六用户 delivery reconciliation 已完成：root 复用可信 Portal/Subscription bundle；Hegin、abing、dangbin、liuwen、zhanhao 各自拥有新的 per-user bundle，旧 Subscription grace hash 保留；OWNER index 只含 User、Plan、bundle path、Portal token status、Subscription status、migration status。
- 公网 projection 实际验证为 Plus=6 条 vless（STANDARD+PREMIUM）、Basic=2 条 vless（STANDARD）、Free=not_configured；没有 AnyTLS 出现在新 Subscription projection。
- 六个 Portal token 的 `/api/me` 均只返回对应 User；跨 User query 仍保持 self-scope，Subscription token 不能作为 Portal credential。

## Rollback and retention

迁移前 DB snapshot 曾仅在变更窗口内保留，并在 acceptance 后删除；当前保留的 post-migration DB snapshot 是 hash-only，旧 code snapshot 不含 User data。若后续需要回滚，先保留 append-preserving Usage ledger，恢复 code 后重新运行 schema/health/Portal/Subscription rejection checks；禁止恢复任何含 plaintext token 的 DB backup。
