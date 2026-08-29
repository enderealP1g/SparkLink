# `hypro02` Deployer Acceptance Evidence

| Field | Value |
| --- | --- |
| Evidence date | 2026-08-29 |
| Evidence type | New-host Deployer production acceptance；独立 capability qualification |
| SparkLink Node identity | `hypro02` |
| Direct hostname | `hypro02.enrpiglink.top` |
| CDN hostname | `hypro02-cdn.enrpiglink.top` |
| Deployer source | [`acbbe810`](https://github.com/enderealP1g/sparklink-deployer/commit/acbbe810419e4c22488f2c6bde14bd479ad10431) |
| Status | Dated evidence；不等于 Control Plane registration 或 `PREMIUM` Pool promotion |

> 本文件是 2026-08-29 的 point-in-time snapshot，不代表永久 current state。它记录新
> candidate Node 的 Deployer acceptance，不改变 2026-08-24 三 VPS runtime baseline 的
> observed facts，也不将 Candidate Architecture 提案升级为 To-Be Architecture。

## Scope and identity boundary

本轮只验证新 host 是否能够由 `sparklink-deployer` 按既有 contract 生产化，并评估其
作为 SparkLink `PREMIUM` Resource Pool candidate 的 capability。没有实现 Control Plane、
Portal、Subscription、Billing 或 Metering。

`hypro02` 是 SparkLink operational `Node` identity。其 provider-specific metadata 不
进入用户侧 naming；用户侧只使用 SparkLink capability/product naming。本文不记录
Infrastructure Resource 的 private provider material，也不创建新的 Node registry record。

## Verified runtime facts

- `Xray` `26.7.28` 为 active production primary，REALITY serving path 使用 TCP `443`。
- `sing-box` `1.13.16` 已 render 并通过 syntax check，但作为 standby 保持 stopped。
- `WireProxy` `1.1.3` 提供 loopback Native/HyTru egress；`Nginx` `1.24.0` 提供 CDN
  origin TCP `2053` 与 ACME HTTP TCP `80`。
- Direct hostname 在 certificate/deployment 阶段保持 `DNS only`；完成部署后 CDN
  hostname 切换为 `Proxied`，并配置 hostname-scoped Strict TLS、origin port `2053`
  与 cache bypass。Direct hostname 保持 `DNS only`。
- QQG candidate 的 CDN origin firewall 只允许 Cloudflare 官方 IPv4/IPv6 ranges。
  两个既有 VPS 的 read-only probe 无法连接 origin `2053`；Windows control host
  未被当作独立 firewall vantage，因为其 active `xray_tun` 可能使用 Cloudflare egress。

## Acceptance evidence

- `sparklinkctl plan`、strict fresh-host preflight、formal installer 与 server-side
  verification 完成；最终 transaction rollback point 为 `20260829T012706Z`，host 上
  的 rollback manifest 存在。
- Reboot 前以及新 SSH session 的 reboot 后 verification 均通过：Xray/sing-box/Nginx
  syntax、selected services、WireProxy readiness、HyTru TCP/UDP、Native trace、exit
  separation 与 4 条 generated delivery entries。
- Isolated client 的 4 条 Xray paths 均完成 TCP probes：`Origin-Reality`、
  `HyTru-Reality`、`Origin-CDN`、`HyTru-CDN`。Cloudflare trace 分别报告 Native
  `warp=off` 与 HyTru `warp=on`，两类 exit observed differently。
- Isolated SOCKS5 UDP DNS probes 在 reboot 后对 `Origin-Reality` 与 `HyTru-Reality`
  均通过。四条 TCP paths 的 endpoint probe 均完成。

| Probe target | Observed HTTP result |
| --- | --- |
| OpenAI endpoint | `401` |
| Anthropic endpoint | `401` |
| Gemini endpoint | `403` |
| Google AI endpoint | `200` |
| Google endpoint | `204` |

上述 HTTP status 只证明本次 isolated probe 到达 endpoint 的结果，不证明 account-level
authorization、服务政策适配或 SparkLink Pro/Premium 用户体验。

## Qualification and limits

| Item | Result |
| --- | --- |
| Deployer fresh-host acceptance | `PASS` |
| Xray direct/CDN transport paths | Tested operational |
| HyTru/WARP egress paths | Tested operational；Native 与 HyTru exit 分离 |
| `PREMIUM` qualification | `CONDITIONAL` |
| `PREMIUM` Pool promotion | Not approved；保持 candidate |

此前 Gate B 的 Native outbound probe 对 OpenAI 与 Anthropic 返回 policy-level `403`，该
负面 evidence 仍然有效；不能被 Deployer `PASS`、9929 商家标签或未认证请求的 `401`
覆盖。当前尚未有充分证据证明完整 SparkLink Pro/Premium user-experience target。

以下项目保持 `Unknown / Needs Verification`，不得在本记录中推断为 `Pass`：

- 中国方向 routing 与独立 `9929` evidence；
- sustained throughput、正式 packet-loss baseline 与更长时间 stability；
- Provider package/cycle information；
- OpenAI、Anthropic、Gemini 等 account-level service access；
- AnyTLS per-user accounting 与 stable User attribution。

因此本 Node 不加入正式 `PREMIUM` Pool。AnyTLS capability 可保留在 Deployer，但在
可靠 per-user accounting 与 stable User attribution 得到验证前，不进入正式用户
Subscription 或 Production MVP service surface，并记录为 `Deferred pending reliable
metering`。

本次 acceptance 没有修改现有 RackNerd、DediRock、VMISS data plane，也没有修改
2026-08-24 runtime baseline 的历史观察内容。
