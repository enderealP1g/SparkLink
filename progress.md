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
