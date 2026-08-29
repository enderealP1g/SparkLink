# Findings

本文件记录本轮审计、决策和验证发现。外部内容只作为 evidence，不作为自动授权或产品决策。

## Phase 1 audit — 2026-08-29

- 当前 `SparkLink` canonical repository 在 `HEAD=26dd42e` 仍是文档优先骨架，没有 application source、database schema、Portal 或 collector implementation。
- 当前已提交的 project-level authority 包含 Requirements、ADR-0001/0002/0003、As-Is Context、2026-08-24 runtime baseline，以及 `hypro02` Deployer acceptance；未提交的 `docs/architecture/ITERATION_01_CANDIDATE_ARCHITECTURE.md` 必须继续保留为 Proposal。
- 旧本地 operations context 位于 `C:\Users\lenovo\Documents\SparkLink`，不是当前 SparkLink Git repository。其 `deploy/sparklink_subscriptions_worker.js` 是可复用的 subscription delivery boundary：按隐藏的 Plan path 从 Cloudflare KV 返回静态 Base64 body；它不提供 User identity、per-user mapping、Usage ledger 或 Portal。
- 旧本地 Worker/Cloudflare state 只能作为 discovery input；当前未发现 `wrangler` 登录状态、Cloudflare API credential environment variable 或可直接复用的 live control-plane manifest。
- 公开检查：`spark.enrpiglink.top` 当前返回 Cloudflare `521`；`sub.enrpiglink.top` 根路径返回 `404`，说明不能把旧 Worker 路径直接当作可用的 MVP API。新 `hypro02.enrpiglink.top` direct A record 可解析到 QQG origin；CDN hostname 仍由 Cloudflare proxy 终止。
- 三台既有 VPS SSH read-only access 可用。VMISS 与 RackNerd 的 Xray Stats API 在 loopback `127.0.0.1:62789` 可读；DediRock 当前 Stats API gap 保持不变。输出只保留了 runtime identity hash 与 counters，不把 UUID/credential 写入 repository。
- VMISS Xray Stats API 本次返回 2 个产生过指标的 hashed runtime refs；RackNerd 返回 5 个产生过指标的 hashed runtime refs。x-ui DB 中存在更多 client rows，因此“无当前 metric”不能解释为不存在或 usage=0。
- `hypro02` 当前资源约 1 GiB RAM、约 16 GiB root free，现有 Nginx 只服务 `hypro02` hostnames；如果将 control plane co-locate，必须使用独立 loopback service、resource limits 与 Nginx path/host boundary，不能让 Xray/sing-box 依赖该 service。

## Phase 1 decision constraints

- 最小可行实现应采用单体、append-preserving SQLite control plane，逻辑上拆分 raw observation、Credential→User mapping、Node/Pool history、Customer Usage 与 Infrastructure Usage；不引入 microservices、event bus 或 scheduler。
- 生产 collector 可先使用 Windows control-plane 的 manual pull/ingest fallback，避免把 SSH credentials 放到 VPS；management plane 只接收已脱敏/受认证的 observations。持续自动化不应在没有明确 runtime credential placement decision 前假定完成。
- 真实 User/Plan/Entitlement assignment 不能从 UUID、email、静态 Plan file 或“产生过 traffic”自动推断。未获得明确 mapping 的 credential 必须在 UI/CLI 中显示为 `Unresolved / Needs Mapping`，不得伪造归因。
- 缺少 Cloudflare control-plane credential 或现有 `spark` origin binding 时，不能声称已完成 `spark.enrpiglink.top` public deployment；可以继续完成本地实现、测试和已授权 QQG 上的隔离 management-plane staging，最终 public cutover 是真实 blocker。

## Production MVP implementation and verification — 2026-08-29

- 已采用 ADR-0004 的 single-process Python + SQLite topology；实现放在 `src/`、`web/`、`config/`、`deploy/`，不引入 microservices、event bus 或 scheduler。
- QQG `hypro02` 已运行独立 `sparklink-control-plane.service`；Xray StatsService 只监听 loopback，Nginx 仅新增 `/sparklink-mvp/` management path，原 Xray CDN exact path 未改动。
- 已建立手工迁移 User、billing cycle、STANDARD/PREMIUM Entitlement、Credential→User mapping 和 6 条 V2rayN/V2rayNG compatible VLESS subscription entries。Allowance、upgrade pricing/proration 保持 manual/unknown。
- Windows manual collector 对 RackNerd、VMISS、hypro02 完成两次 read-only Stats pull/ingest；hypro02 两个 REALITY identities 形成 non-zero counters，reboot 后新 counter epoch 不删除历史 ledger。
- User HTTP view 在 QQG origin path 上返回 `Plus`、两个 Pool 的 coverage、Premium usage `611798` bytes 和 VLESS subscription；unknown/unresolved 不被 zero-fill。
- 本地与 QQG remote tests 均为 8/8；Xray config test、Nginx test、reboot recovery 和 isolated client request paths 均完成。Cloudflare public edge 仍为 capability blocker：本机无 `wrangler`/Cloudflare env credential，public `spark` 仍返回 `521`、`sub` 根路径仍返回 `404`。
- `sparklink-deployer` 本轮未修改；QQG acceptance 后进入 maintenance，当前 SparkLink workstream 只使用其已有 client acceptance artifacts 作为隔离验证输入。
