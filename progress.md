# Progress Log

## 2026-08-29

- 已创建本轮 Production MVP vertical slice 计划。
- 已确认 SparkLink 当前存在未提交的 Candidate Architecture Proposal；本轮需保留，不静默覆盖。
- 下一步：读取 root `AGENTS.md`、Requirements、ADR、As-Is/Runtime baseline、Acceptance 文档，并审计代码入口。
- 已完成上述 source-of-truth、旧 Worker、DNS、tooling、SSH connectivity 和三 VPS Stats API 的只读审计；关键结果已写入 `findings.md`。
- 发现：当前 SparkLink repo 没有 application source；Cloudflare control-plane credential/tooling 未在本机发现；真实 User/Plan mapping 仍必须由 runtime/admin mapping 提供，不能从 technical identity 自动推断。
- 已在 QQG `hypro02` 部署 loopback-only management plane，服务监听 `127.0.0.1:8080`；原有 Xray/Nginx/WireProxy 初始状态未改动。
- 已创建手工迁移 `User`，并保留 allowance/cycle 不确定性；8 个已知 Xray/VLESS runtime identity 已映射，DediRock 未进入正式 subscription/metering。
- 本地修复了 User view 的 unresolved attribution 隔离和 missing-counter fail-closed 语义，7 个单元测试通过。
- 第一次 StatsService staged config test 被安全拒绝，第二次 syntax test 通过但重启后验证触发 rollback；rollback 脚本曾将 `/etc/xray/config.json` 保留为 root-only，导致 Xray 临时进入 auto-restart。已恢复 `xray:xray` ownership、原有 `0600` 权限并重新验证 Xray active、443/10080 listeners；Stats API 尚未启用。
- StatsService 已以 loopback-only 方式启用；Xray/Nginx/WireProxy/Control Plane reboot recovery 通过，Xray counter epoch 变化不会重写旧 ledger。
- 已导入 6 条 `vless` subscription entries（RackNerd Standard、VMISS Premium、hypro02 REALITY Premium），AnyTLS、DediRock 和 CDN standby identities 未进入正式 User surface。
- 两次 collector ingest 后，manual Plus User 的 Standard used=`0`、Premium used=`611798` bytes、total=`611798`，两 Pool coverage=`available`；未配置 allowance 保持 unknown/NULL。
- QQG Nginx `/sparklink-mvp/` origin path、Portal、Bearer API 和 Bearer subscription HTTP smoke 通过；Cloudflare public `spark`=`521`、`sub` root=`404`，本机无 `wrangler`/Cloudflare credential，因此 public cutover 停在人工 blocker。
