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
- QQG Nginx `/sparklink-mvp/` origin path、Portal、Bearer API 和 Bearer subscription HTTP smoke 通过；此前 public `spark`=`521` 的 blocker 已通过 Wrangler OAuth deployment 解除。仅为 `spark.enrpiglink.top/*`、`sub.enrpiglink.top/*` 创建 `sparklink-edge` Workers Routes；其他 Worker/route/DNS 未修改。
- 公网 acceptance 通过：Portal root/healthz=`200`，真实 User API 返回 `Plus`、两 Pool `available` coverage；subscription=`200` 且返回 6 条 `vless` entries。错误 token=`401`、错误路径=`404`。
- 全新 isolated v2rayN `7.18.0` 通过自身 subscription updater 获取公网 subscription 并生成 6 个 `VLESS/REALITY` profiles；因现有 live v2rayN 占用 `10808`，未启动 isolated local proxy，实际外连由临时 isolated Xray client 验证。
- 公网 subscription 派生的 isolated Xray client 实际验证 hypro02 Origin/HyTru `VLESS/REALITY`；两条 path 的 Google=`204`、Google AI=`200`、OpenAI/Anthropic=`401`、Gemini=`403`。collector 随后 ingest hypro02 两个 observations，Premium Usage 从 `611798` 增长到 `1223310` bytes。

## 2026-08-29 — Production Hardening & Metering Completion

- 已暂停新增用户可见 feature 与 production service-surface expansion；Candidate Architecture 继续保持 Proposal 状态。
- 当前阶段 H1–H4 已建立：先做 sing-box 1.13.16 AnyTLS 隔离 accounting evidence，再审计 Xray collector/ledger，随后实现 automatic collector service；manual collector 保留为 fallback。
- 已完成远端 AnyTLS 隔离 evidence：official installed binary 明确缺少 `with_v2ray_api`；独立 custom test build 对两个 synthetic users 验证 per-user uplink/downlink、Origin/HyTru inbound boundary、reset、restart/missed collection、credential rotation 与 loopback/data-plane isolation。结论保持 `Deferred pending reliable metering`，详细证据见 `docs/operations/2026-08-29_ANYTLS_ACCOUNTING_INVESTIGATION.md`。
- AnyTLS test harness 的两次修正（SOCKS outbound `version` 类型、V2Ray compatibility service name）均已记录在 `findings.md`；临时 process/listener 已清理，installed runtime 未替换。
- H2/H3 placement discovery：QQG 只有 `authorized_keys`，没有跨 Node SSH alias/key；因此 automatic collector 不搬到 QQG，不改变其 runtime。计划在已有 Windows protected control-plane SSH context 上实现可被 OS supervisor 管理的 long-running collector，QQG 继续仅承载 Control Plane。
- H2 已完成：Xray ledger 已覆盖 same-epoch reset/non-monotonic、process epoch、duplicate/conflict、late observation、partial Node failure、empty source、coverage freshness 与 timezone validation；local regression suite 最终 `17 tests` passed。
- H3 已完成：automatic collector 使用 Windows `Task Scheduler` 作为 OS supervisor，Python interval loop 通过 SSH read-only pull 与 loopback tunnel 连接 QQG；one-shot collector 保留为 manual fallback，不引入 product scheduler。
- H4 已完成：repository ignored runtime 的 `LocalMachine DPAPI` secret 修正了 Task Scheduler 的 `AppData` 读取边界；interactive one-shot 与自动 task 连续两个 interval 均完成 3/3 Nodes ingest、`failed=0`。QQG Control Plane hardening backup、health/service/listener retest 通过，production proxy data plane 未修改。
- H1–H4 完成后的结论：AnyTLS 继续 `Deferred pending reliable metering`；Xray automatic collection 为正常路径，manual collection 为 fallback。

## 2026-08-29 — Production Identity, Subscription & Cycle Reconciliation

- Product Owner 裁决已读取；Production Hardening commit `ae1259f09dd6e039184fd1e56ec9f44e142c5ad9` 已 push 到 `origin/main`。
- Cycle discovery 完成：Customer Cycle 固定 `Asia/Shanghai` 15→15，自 `2026-09-15 00:00` 生效；四个 VPS 的实际 OS timezone 只读验证均为 `Etc/UTC`，Provider Resource Cycle 不按用户 timezone 或地理位置推断。
- Live DB reconciliation baseline 已取得：1 个旧手工 User、1 个 legacy-like active cycle、8 条 credentials；下一步保留历史 Usage，先实现 cycle model 和六用户 identity migration 的本地 tests。

## 2026-08-29 — Hardening revalidation and identity/cycle completion

- AnyTLS investigation remains `Deferred pending reliable metering`；official installed `sing-box 1.13.16` 未替换，隔离 custom build evidence 已保留在 dated operations record。
- 修正 isolated Xray Stats probe 的测试代理绕过问题：移除 `curl --noproxy *`，改为清除环境代理后保留 SOCKS；QQG `hypro02` real managed User 的 VLESS transfer 经 server log、Stats API 与 ledger `baseline + delta` 三方核对。
- 加固 collector remote parser：每个 runtime identity 必须同时有 uplink/downlink；partial、malformed、duplicate rows 或无法确认 Xray process epoch 均形成 coverage gap，不补 synthetic zero。兼容 `x-ui` 管理的 `xray-linux-amd64` process。
- Windows automatic collector 改由 `sparklink-collector-run.ps1` 负责 tunnel/process supervision，protected secret 由 Python 通过 `--secret-path` 解密，不通过 wrapper environment 传递；startup cleanup 仅匹配本 task 的固定 SSH forward。
- 当前 live automatic interval 可重复得到 `1 ingested / 2 unknown / 0 failed`：`hypro02` 有 per-user counters，RackNerd/VMISS StatsService 可达但 identity migration 后没有当前 counter rows；Unknown 不表示 zero。task stop/start 已验证无残留 matching SSH tunnel 阻塞下一次启动。
- `30 tests`、`compileall` 与 PowerShell parse checks 通过；Control Plane 入口也已拒绝 incomplete/fractional counter，避免缺失方向或截断值被补成可信 Usage。identity/cycle reconciliation 的 canonical ADR 与 operations record 已补齐。下一步为最终 secret scan、diff review、live service recheck 与本地 commit checkpoint。

## 2026-08-29 — Production Operations Token Issuance & Delivery

- Product Owner 确认当前第一个 blocker 是 Admin credential issuance/delivery；其它 Bug Hunt 暂停，先处理 Portal/Subscription token 的安全 issuance、rotation、delivery 与 rejection verification。
- Read-only code audit 已确认 `users.subscription_token` 仍为 plaintext storage，当前没有 Admin issuance workflow；下一步先改本地 schema/API/Windows operator path，再做远端 backup/deploy。

## 2026-08-29 — Production Operations Token Issuance & Delivery complete

- 本地实现和完整测试通过：`38 tests`、`compileall`、`git diff --check`；覆盖 hash-only migration、旧/wrong/cross-kind rejection、Admin authorization、one-time protected bundle、ACL/ignored path 和 owner acceptance validator。
- QQG Control Plane 已部署并重启迁移；live users schema 无 legacy plaintext 列，6/6 用户两类 hash 有效，foreign-key clean，service active，loopback health `ok`，现有业务计数保持不变。
- root `usr_plus_manual_01` 新 Portal token 已写入 Windows ignored protected delivery bundle；bundle 未进入 Git，ACL 仅当前 operator。真实 Portal acceptance 通过：root / Plus / OWNER / `legacy-pre-baseline` / `Asia/Shanghai` / STANDARD + PREMIUM / `/api/me` self-scope。
- live rejection verification 通过：本轮 superseded old Portal、wrong Portal、Portal-as-Subscription、wrong Subscription、Subscription-as-Portal 均 rejected；当前 root Subscription URL 未 rotate 且 accepted。
- Admin list 已确认 6 个 User 可由同一 workflow 指定交付；没有自动 rotate 其它 User。迁移前 plaintext DB rollback copies 已删除，保留 post-migration hash-only snapshot；现在恢复原 Bug Hunt。

## 2026-08-29 — Six-user delivery reconciliation complete

- Product Owner priority override remains active：Bug Hunt paused；本阶段只处理六个现有 User 的可发送 credential delivery closure。
- Control Plane 增加可选 hash-only `subscription_token_legacy_hash` grace slot。Portal issuance 仍立即 revoke 旧 Portal；staged `both` issuance 只替换 Portal 并保留旧 Subscription hash，支持验收后显式 `revoke-legacy`。
- live Admin metadata 固定确认六个 User：root/Hegin/abing 为 Plus，dangbin 为 Basic，liuwen/zhanhao 为 Free；projection 为 Plus 6 条 vless（STANDARD/PREMIUM）、Basic 2 条 vless（STANDARD）、Free `not_configured`，AnyTLS 未进入新 projection。
- `python deploy\\issue_user_tokens.py reconcile` 已完成：root 复用可信 bundle；其余五个 plaintext 已不可恢复的 User 各自 issue 新 Portal + Subscription，并在新 URL 公网 fetch/projection 验证前保留旧 Subscription grace hash。
- 本机已生成 `runtime/delivery/<username>/delivery.json` 六份和 OWNER-only `OWNER-DELIVERY-INDEX.json`；`copy --user <username> --kind portal|subscription` 为显式 local-only clipboard path，stdout 不输出 secret。
- 独立验收通过：6 个 Portal self-scope、30 个跨用户 query self-scope、6 个 Subscription-as-Portal rejection；六个 credential pairwise unique；runtime delivery 7 个文件 ACL 仅当前 operator、全部 ignored、无 tracked delivery 或 tracked token 命中。
