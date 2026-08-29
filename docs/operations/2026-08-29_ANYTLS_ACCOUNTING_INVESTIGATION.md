# 2026-08-29 sing-box 1.13.16 AnyTLS accounting investigation

| Field | Value |
| --- | --- |
| Status | `Deferred pending reliable metering` |
| Evidence date | 2026-08-29；QQG `hypro02` point-in-time test |
| Scope | 隔离验证 `sing-box 1.13.16` AnyTLS per-user accounting；不改变 production Subscription |
| Runtime change | 未替换 installed binary；未启用现有 `sing-box.service`；未修改 Xray/Nginx/WireProxy/Control Plane |
| Related policy | [`ADR-0003`](../decisions/0003-iteration-01-runtime-and-metering-boundary.md)、[`ADR-0004`](../decisions/0004-production-mvp-vertical-slice.md) |

本文件记录一次 point-in-time isolated evidence，不代表 QQG 或其他 Node 的永久 current state。测试使用非 production identity、loopback-only temporary listeners 和临时 custom binary；所有 password、UUID、token、private key 与完整 runtime URI 均不写入 Git。

## Conclusion

`users[].name` 在 custom `v1.13.16 + with_v2ray_api` build 中可以作为可观察的 per-user counter label，但当前不能满足 SparkLink 的可靠 production attribution contract，因此 AnyTLS 继续保持 `installed/standby capability`，不进入 production Subscription 或 Production MVP formal service surface。

阻塞结论由三个独立事实共同构成：

1. QQG 当前 installed official binary 的 build tags 不包含 `with_v2ray_api`；使用含 API 的临时 config 会明确失败，不能直接提供该 API。
2. custom build 的 V2Ray Stats counter 属于 sing-box process instance；`QueryStats(reset=true)` 是 destructive read，process restart/reload 前未读的 counter 可能丢失。仅增加 polling frequency 不能消除这个 loss window。
3. `users[].name` 是 runtime configuration label，不是 SparkLink durable `Credential` 或 `User` identity。credential rotation、inbound/path boundary 和 Node/Pool/cycle attribution 仍需由外部 durable mapping 与 observation contract 保障。

## Evidence boundary and test topology

测试在 QQG 上临时建立以下 topology，所有 test listener 只绑定 loopback：

```text
synthetic client A/B
        ↓ AnyTLS
temporary sing-box server
  ├─ anytls-origin : loopback test port → direct
  ├─ anytls-hytru  : loopback test port → existing local SOCKS/WireProxy path
  └─ V2Ray Stats API : loopback test port only
        ↓
temporary loopback HTTP target / public 204 probe
```

test server 使用两个隔离 `users[].name`：`synthetic-user-a` 与 `synthetic-user-b`。测试 password 只存在于 QQG 临时文件和 process memory，未被记录。

## Observed / Verified Fact

### Package and build capability

- QQG `/usr/local/bin/sing-box` verified as `1.13.16`，官方 binary 的 `Tags` 不含 `with_v2ray_api`。
- QQG 当前 `sing-box.service` 为 `inactive/disabled`；现有 `/etc/sing-box/config.json` 的脱敏结构包含 1 个 `anytls` inbound、2 个 users，但没有 `experimental.v2ray_api`。
- 对含 `experimental.v2ray_api` 的 temporary config 运行 installed binary 的 `check` 返回：`v2ray api is not included in this build, rebuild with -tags with_v2ray_api`。
- 使用 official `v1.13.16` source 加 `with_v2ray_api` 在 QQG 临时目录编译的独立 binary 通过相同 config check，并启动了 loopback-only gRPC Stats API。该 binary 没有安装到 `/usr/local/bin`，也没有注册为 systemd service。

### User isolation and direction semantics

- 对 `synthetic-user-a` 执行已知大小的 `65,536` bytes download 和 `32,768` bytes upload，target 均返回成功；Stats API 返回该 user 独立的：`uplink=33,018`、`downlink=65,805` bytes。
- 随后只使用 `synthetic-user-b` 执行同样 transfer：B 的 counters 增加相同数值，而 A counters 保持不变；在 A transfer 后 B 没有提前产生 counters。
- Stats API 的 `uplink/downlink` 与 client→server、server→client 方向一致；counter 包含 HTTP request/response 与 protocol overhead，因此不等于 target payload size。这是 counter direction evidence，不是 payload-only billing rule。
- 结果实际出现 `user>>>{users[].name}>>>traffic>>>{uplink|downlink}` 两类 label，证明 custom build 可以观察 `users[].name` 维度。

### Origin / HyTru boundary

- `anytls-origin` inbound 的 direct path 产生独立 inbound counters。
- `anytls-hytru` inbound 经现有 loopback SOCKS/WireProxy path 访问 public `https://www.google.com/generate_204`，返回 HTTP `204`；该 inbound 产生 `uplink=849`、`downlink=4,889` bytes 的独立 counters。
- 同一 `synthetic-user-a` 的 user counter 在 HyTru test 中增加与该 inbound counter 相同的数值；因此 user counter 会跨 inbound/path 聚合，而 inbound counter 才能区分本次 Origin/HyTru observation boundary。仅保存 `users[].name` 不足以表达 SparkLink 所需的 path boundary。

### Counter reset, restart and missed collection

- `QueryStats(reset=true)` 返回当时 counters；紧接着 non-reset query 返回同一组 counter 的 zero values。该 reset 行为已验证为 destructive read，不是可重复读取的 snapshot。
- 在产生一段真实 transfer 后故意停止 temporary server、且不先 query counters；restart 后 process epoch 改变，新的 Stats API 返回 empty user counter set。未采集的 bytes 没有从该 API 恢复。
- process epoch 由 host boot context 与 process start context 在测试脚本中独立计算；restart 前后 epoch changed。该 evidence 支持 collector 必须记录 process/counter epoch，而不能只使用 host boot ID。

### Credential rotation

- 使用旧 credential 连接 rotation 后的 temporary server 得到 connection reset/HTTP `000`；使用新 credential 成功取得 HTTP `200`。
- rotation 保持相同的 `users[].name`，但伴随新的 process/counter epoch。由此可见，name 的连续性必须由外部 `Credential → User` mapping 解释；sing-box counter API 本身不会提供 SparkLink stable `User` identity 或历史 mapping。

### API exposure and data-plane isolation

- temporary Stats API 只监听 loopback；没有把 API 暴露到 public interface。
- test 前后 `xray`、`nginx`、`sparklink-wireproxy.service` 与 `sparklink-control-plane.service` 均为 `active`；Xray `443`/`10080`/`62789`、Nginx `2053` 和 Control Plane `8080` listeners 保持存在。
- temporary AnyTLS server/client/HTTP target 结束后无 temporary process 或 test listener 残留；installed `sing-box.service` 仍为 `inactive`。因此本次 evidence 未改变既有 proxy data plane。

## Known from official source / package context

- `v1.13.16` 官方 configuration 文档说明 V2Ray API 是 gRPC，且 `with_v2ray_api` 不是默认 build tag；Stats 配置可选择 `inbounds`、`outbounds` 与 `users`。
- `v1.13.16` source 的 Stats implementation 对 user 生成 `user>>>{name}>>>traffic>>>{uplink|downlink}`，并支持 `QueryStats` 的 reset 语义。这解释了本次 live test 的 label/direction 结果，但 source reading 本身不替代 live evidence。
- 官方 source issue 中记录的 process reload 行为与本次 isolated restart evidence 一致：Stats service 属于 process instance，reload/restart 期间未完成读取的 counter 不能被当前 API 自动恢复。

## Architecture Inference

- custom build 的 per-user stats 可以作为 future AnyTLS observation adapter 的 input，但不能直接作为 durable `Usage` ledger 或 hard quota authority。
- 若未来同一 User 同时使用 Origin 与 HyTru，adapter 必须同时保留 user label、inbound/path、Node、observation time、process/counter epoch 与 direction；不能只保存 aggregated user counter。
- `Credential → User → Node → Resource Pool → Usage` 的 stable attribution 仍需要 SparkLink durable mapping。`Subscription` 不能替代该 mapping，也不能从 AnyTLS `users[].name` 自动推断。

## Unknown / Needs Verification

- 当前 installed package 是否会在未来版本以官方方式提供 `with_v2ray_api`，以及 SparkLink 是否批准维护独立 signed/pinned custom build，尚未裁决。
- production-safe 的 restart/reload handoff、counter flush、API response-loss recovery 与 duplicate/out-of-order reconciliation 尚未实现或接受。
- 本次 test 只证明 loopback temporary direct/HyTru topology；不证明任一 current production AnyTLS path 已有可上线的 stable User mapping、Node/Pool history 或 Customer Billing Cycle attribution。
- AnyTLS protocol overhead 是否应计入 SparkLink Customer Usage、以及用户视图的 accounting boundary，仍需 Product Owner/Architecture decision。

## Decision and safe next step

当前结论：`AnyTLS production promotion = Deferred pending reliable metering`。

在没有新的 reviewed decision 前：

- 不升级或替换 QQG、VMISS、DediRock 或其他 production runtime 的 sing-box binary；不启用现有 QQG `sing-box.service`。
- 不向 AnyTLS 写入正式 production Subscription，不把未观测 traffic 记为 zero，也不把 custom test result 当作现有 User Usage。
- 如需再次评估，最小安全前提是单独审查 build provenance、loopback-only API、stable unique name mapping、process epoch、restart/reload loss handling、Origin/HyTru path metadata 和 isolated acceptance regression。
- Xray 继续作为当前 Production MVP formal metering surface；AnyTLS 保留为 extension boundary，直至上述 blocker 有新的 evidence 与 Product Owner approval。

## Source references

- [sing-box v1.13.16 V2Ray API configuration](https://raw.githubusercontent.com/SagerNet/sing-box/v1.13.16/docs/configuration/experimental/v2ray-api.md)
- [sing-box v1.13.16 build tags](https://raw.githubusercontent.com/SagerNet/sing-box/v1.13.16/docs/installation/build-from-source.md)
- [sing-box v1.13.16 AnyTLS inbound](https://raw.githubusercontent.com/SagerNet/sing-box/v1.13.16/docs/configuration/inbound/anytls.md)
- [sing-box v1.13.16 Stats implementation](https://raw.githubusercontent.com/SagerNet/sing-box/v1.13.16/experimental/v2rayapi/stats.go)
- [V2Ray Stats process-reload loss issue](https://github.com/SagerNet/sing-box/issues/4059)
