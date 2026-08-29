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

## Production Hardening & Metering Completion — 2026-08-29

### Scope and evidence boundary

- 本轮目标是验证 AnyTLS per-user accounting、加固 Xray usage ledger，并以最小方式替换 Windows manual collection 的正常运行路径；不扩展 Portal、payment、scheduler 或其他 user-visible feature。
- AnyTLS 结论必须来自隔离 identity 与真实 transfer evidence；sing-box 文档或配置 schema 只能说明可测试方式，不能单独证明 `users[].name` 可作为稳定 accounting dimension。
- 生产验证必须保持 `proxy data plane` 与 management/metering plane 解耦；任何临时测试需使用可清理的隔离 runtime、非正式 User identity 和脱敏输出。

### H1 initial live/package evidence

- QQG live read-only discovery（2026-08-29）确认 `/usr/local/bin/sing-box` 为 `1.13.16`，`Tags` 不含 `with_v2ray_api`；systemd `sing-box.service` 当前 `inactive/disabled`，不属于当前 Xray production path。
- QQG `/etc/sing-box/config.json` 的脱敏结构显示存在 1 个 `anytls` inbound、2 个 users；未配置 `experimental.v2ray_api`。不得把该配置当作 per-user accounting evidence。
- 官方 `v1.13.16` source 的 build metadata/documentation 表明 `with_v2ray_api` 是 opt-in build tag，Stats API 是 gRPC；source implementation 为 user counter 生成 `user>>>{name}>>>traffic>>>{uplink|downlink}`，这只是 capability lead，仍需 live isolated traffic verification。
- 已在 QQG 临时目录用官方 `v1.13.16` source + `with_v2ray_api` 编译出独立 test binary；未替换 installed binary、未启用 sing-box service。该 binary 的 `version` 可运行，后续用于隔离 AnyTLS/API test。
- 官方 source issue evidence 还提示 V2Ray Stats counters 归属于 sing-box process instance；reload/restart 前未采集的增量可能丢失，因此即使 per-user stats 测试通过，也不能直接作为 production billing authority，必须把 restart/reload epoch 与 recovery limitation 纳入结论。
- 隔离测试第一次 `sing-box check` 失败原因为临时 harness 的 SOCKS outbound `version` 使用了 number；`v1.13.16` schema 要求 string。测试未启动任何 temporary proxy process，已记录为 harness correction，不能作为 AnyTLS capability failure。
- 第二次 harness 在 custom build 启动后使用了 generated protobuf 的默认 service path；该版本 source 在 init 时将 service name 改为 V2Ray compatibility name，因此 probe 得到 `Unimplemented`。已按 source 的实际 registered path 修正 probe；这次测试也验证了 custom API process 已在 loopback listener 上启动。

### H1 conclusion

- AnyTLS isolated evidence confirms observable `users[].name` counters only in a separately built binary; the installed package lacks the API. `AnyTLS production promotion` remains `Deferred pending reliable metering` because the counter lifecycle and durable SparkLink attribution contract are not satisfied.

### H2/H3 deployment placement discovery

- QQG `/home/codexops/.ssh` has only `authorized_keys` and no read-only SSH alias/config or cross-Node identity suitable for the RackNerd/VMISS pull path. No SSH key will be copied to QQG for this task.
- QQG Control Plane environment and database are available only on protected runtime paths; current DB has 43 observations/ledger rows, 15 coverage events, 8 credentials and 1 User. This confirms the automatic path must preserve existing data and work around a live SQLite database, not recreate it.
- The minimal automatic collector should therefore remain a management-plane pull process on the protected Windows control plane, where the existing SSH aliases/keys already live; an OS service supervisor may restart that process, but this is not a product scheduler. QQG continues to host only the Control Plane and does not receive collection private keys.

### H4 acceptance evidence

- 受保护的 `LocalMachine DPAPI` admin token 已放置于 repository ignored `runtime` location；interactive one-shot collector 通过 QQG SSH tunnel 成功完成 3/3 Nodes ingest，`failed=0`。运行输出只包含 node/status/counter 结果，不包含 token 或其他 runtime secret。
- Windows `Task Scheduler` task `SparkLink-Metering-Collector` 已重新注册为直接启动 Python interval collector，Working Directory 为当前 repository，SSH tunnel 指向 QQG loopback Control Plane；Task 状态为 `Running`，连续两个 interval 均记录 `attempted=3`、`ingested=3`、`unknown=0`、`failed=0`。
- 早期 task run 使用 `AppData` secret path 时因该 scheduler context 读取不到文件而失败；identity probe 证实同一 context 可读取 repository runtime path。临时 probe 已清理，旧 failure log 未被伪装为成功。
- QQG Control Plane hardening deployment 保留 `/var/backups/sparklink-control-plane/hardening-20260829T062550Z/sparklink_control_plane.py` rollback file；restart 后 health、Xray/Nginx/WireProxy/Control Plane services 与既有 listeners 保持通过。未修改 RackNerd、DediRock、VMISS proxy data plane。
- Local regression suite 最终为 `17 tests` passed，`compileall` passed；PowerShell deployment scripts parse successfully。Automatic collector 与 one-shot fallback 均通过独立 command path 验证。

## Production Identity, Subscription & Cycle Reconciliation — discovery

- Product Owner 已明确 `Customer Cycle` 使用 `Asia/Shanghai`，policy baseline 为 `2026-09-15 00:00`，周期为每月 15 日至次月 15 日；2026-09-15 之前的历史 Usage 必须保留并标记 `legacy/pre-baseline`，不得进入新商业周期 enforcement。
- 对 `sparklink-node-166`、`la9929`、`racknerd-admin`、`dedirock-admin` 的只读 timezone discovery 均返回 `Etc/UTC`。因此 Provider Resource Cycle 的 timezone 必须记录为已验证的 instance local timezone `Etc/UTC`；洛杉矶/纽约是 location metadata，不得替代实际 host timezone。
- QQG live SQLite 当前有 1 个 active User（旧手工 Plus migration）、1 个 `manual-ops-current` active cycle、8 条已登记 Credential、现有 VLESS subscription entries，以及 291 条 observations/ledger rows 和 97 条 coverage events。后续 reconciliation 必须保留这些历史记录，不重建或清空数据库。
- 当前 live DB 中已有的 8 条 Credential 仍指向旧手工 User 或 standby/unresolved 状态；六用户正式 identity migration 尚未开始。不能从现有 UUID、subscription entry 或 traffic 自动推导 User ownership。
- 一次通过 PowerShell `Write-Output` 管道传送只读 inventory script 时出现 `base64: invalid input`，但 remote process 仍输出了可解析 inventory；该传输方式不作为后续 mutation/evidence channel，后续使用短命令或可靠 stdin transport。

## Production Hardening revalidation — 2026-08-29

### AnyTLS and Xray Stats evidence correction

- 早期 temporary Xray probe 使用 `curl --noproxy *`，导致 HTTP 204 不是经过 temporary SOCKS/VLESS path；该结果不作为 traffic evidence。修正为清除 inherited HTTP/SOCKS proxy environment、保留显式 SOCKS proxy 后，server access log 确认收到 `www.google.com:443` VLESS request，Xray `StatsService` 返回 synthetic user 的 uplink/downlink counters。
- 同一 corrected boundary 下，QQG `hypro02` real managed User 的 loopback VLESS transfer 成功；两次 collector ingest 形成 `baseline + delta`，ledger 增量 `4,414` bytes。该 evidence 支持 Xray Stats observation surface，不改变 AnyTLS 的 Deferred 结论。

### Collector/ledger hardening

- Collector 现在拒绝缺少任一 direction、malformed counter、duplicate runtime row 或缺失 process epoch 的 source response；拒绝结果只写 coverage gap，不发送 observation、不把缺失方向补成 zero。
- `counter_epoch` discovery 对 `xray` systemd unit 使用实际 MainPID；RackNerd/VMISS 的 Xray 由 `x-ui` 管理时回退读取实际 `xray-linux-amd64` process。三 Node 当前 source query 均可返回稳定 epoch；RackNerd/VMISS 当前没有 counter rows，因此 runtime result 为 `unknown`，不是 `0`。
- local regression suite 当前 `30 tests` passed，覆盖 same-epoch reset、new epoch/restart boundary、duplicate/conflict、out-of-order、partial failure、partial counters、malformed numeric counters、empty source、stale coverage、cycle boundary、timestamp validation 与 Control Plane incomplete-counter rejection。

### Automatic collector current state

- Windows `SparkLink-Metering-Collector` 使用 PowerShell launcher 管理 SSH tunnel 与 Python interval process；Python 从 protected DPAPI path 解密 admin secret。Task 当前 `Running`，连续 interval 为 `1 ingested`（`hypro02`）、`2 unknown`（RackNerd/VMISS）、`0 failed`。
- Task stop 会由 Windows 留下 child SSH process；launcher 的下一次 startup cleanup 只匹配自身固定 forward/host，已验证 stop/start 后没有残留 matching tunnel 阻塞 recovery。非 matching listener 仍按 blocker 处理，不被自动清理。
- QQG Xray、Nginx、`sparklink-wireproxy`、Control Plane 与 expected listeners 的 read-only check 通过；collector changes 未修改 proxy data-plane configuration。

### Identity/cycle reconciliation current state

- 六个 stable User、managed/legacy Credential mapping、独立 Subscription token、Customer Billing Cycle 与 Provider Resource Cycle metadata 已完成 live reconciliation；具体脱敏证据见 `docs/operations/2026-08-29_PRODUCTION_IDENTITY_CYCLE_RECONCILIATION.md`。
- `Customer Billing Cycle` 继续使用 `Asia/Shanghai` 15→15；四个 host instance local timezone 以 read-only evidence 记录为 `Etc/UTC`。provider traffic reset authority 仍 Unknown，不由 Next Due 或 location 推断。
