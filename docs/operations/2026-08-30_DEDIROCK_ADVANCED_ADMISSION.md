# 2026-08-30 DediRock Advanced admission

本记录描述 DediRock 进入 BASIC/PLUS personal Subscription 的 Production Operations admission。它不启用 P7 hard quota/automatic blocking，不撤销尚未确认迁移的 legacy/shared access，也不把 provider/host/Nginx aggregate 当作 User Usage。

## Admission result

| Item | Evidence |
| --- | --- |
| Runtime path | Direct `dedirock.enrpiglink.top:443` Xray VLESS Reality ingress with separate native/direct and HyTru/WARP egress |
| Managed Users | `root`, `Hegin`, `abing`, `dangbin` |
| Runtime identity action | Four existing stable HyTru identities reused; four separate Origin identities added |
| Isolated client acceptance | 8/8 real transient Xray clients: Origin `warp=off`, HyTru `warp=on` |
| Control Plane | DediRock `active/verified`, `ADVANCED`, VLESS access/subscription allowed |
| Metering | `Unknown`; no per-user Stats source is available |
| Quota | `unavailable`; 700GB remains policy-only |
| Legacy/shared access | Unchanged |

After admission, a read-only route audit found that the four managed Advanced
identities were not covered by the existing static HyTru rules and all four
isolated checks observed `warp=off`. The reversible repair command added one
exact-user `outboundTag=warp` rule. The dual-route operation then added four
separate Origin identities and one exact-user `outboundTag=direct` rule. Xray
configuration validation and service recovery passed; eight fresh isolated
public checks observed the expected route identity (`warp=off` for Origin,
`warp=on` for HyTru). A second run was idempotent and reused the existing
runtime identities.

The route rollback artifact is root-only at
`/var/backups/sparklink-identity-migration/20260830T070546Z-dedirock-hytru-route/xray-config.json`
with directory mode `0700` and file mode `0600`. A structural comparison
confirmed that only the routing section changed; all clients, outbounds, and
other top-level configuration remained equal. Xray, Nginx, and sing-box were
active after acceptance.

The admission runner is idempotent. The initial Control Plane registration
created four managed HyTru credentials and four current Advanced projection
entries. The dual-route operation added four separate Origin credentials and
four current Advanced projection entries; a follow-up run reused all sixteen
runtime/projection records and did not create duplicate active membership.
Runtime identities are keyed by stable managed Xray email references; the
Control Plane stores only the corresponding reference hashes.

## Protected rollback evidence

The DediRock config baseline is retained root-only at:

`/var/backups/sparklink-identity-migration/admission-baseline-20260830T043755Z-dedirock/xray-config.json`

The recorded config SHA-256 is `25343c73ed4796c91229ff4a34554fd9f23cd7d8215ac3d1d991ef86218d8cc0`. The file is a protected runtime backup and is not copied into Git or operations documents beyond this path/hash reference. If a future identity mutation is required, `deploy/apply_xray_identity_migration.py` creates a new root-only backup, checks the discovery SHA, validates Xray before restart, and restores on failure.

The management-plane code deployment used a separate protected backup:

`/var/backups/sparklink-control-plane/runtime-admission-20260830T044354Z/sparklink_control_plane.py`

## Projection and metering

After admission and six-bundle refresh, the current public personal projections are:

| User | Plan | Current VLESS entries | DediRock Advanced | Usage |
| --- | --- | ---: | --- | --- |
| root | Plus | 8 | present (Origin + HyTru) | Unknown |
| Hegin | Plus | 8 | present (Origin + HyTru) | Unknown |
| abing | Plus | 6 | present (Origin + HyTru) | Unknown |
| dangbin | Basic | 4 | present (Origin + HyTru) | Unknown |
| liuwen | Free | 0 / not configured | absent | Not applicable |
| zhanhao | Free | 0 / not configured | absent | Not applicable |

The Windows collector uses `metering_mode=unknown` for DediRock. Live evidence is `attempted=4`, `ingested=3`, `unknown=1`, `failed=0`; the formal heartbeat is `degraded` only because one configured Node has no per-user source. DediRock remains visible in per-Node access and Usage views as `Unknown`, never `0`.

## Operator workflow

From the repository root on the Windows control machine:

```powershell
python deploy\admit_dedirock.py
python deploy\repair_dedirock_hytru.py preview
python deploy\repair_dedirock_hytru.py apply
python deploy\ensure_dedirock_dual_routes.py preview
python deploy\ensure_dedirock_dual_routes.py apply
python deploy\issue_user_tokens.py reconcile
python deploy\issue_user_tokens.py copy --user Hegin --kind portal
python deploy\issue_user_tokens.py copy --user Hegin --kind subscription
python deploy\admin_console.py
```

The admission command emits only safe counts/status/path metadata. The six user delivery bundles remain in ignored, ACL-protected `runtime\delivery\<username>\delivery.json`; the OWNER index lists metadata only. DediRock runtime URIs, UUIDs, key values, Portal tokens, and Subscription tokens are never printed or stored in this document.

## Deferred boundary

DediRock XHTTP/Cloudflare, ShadowTLS, and AnyTLS paths remain outside the Advanced VLESS projection. Reliable per-user DediRock metering is still an open provider/runtime evidence task; no hard quota, automatic blocking, or synthetic attribution is allowed. Direct Reality ingress and HyTru/WARP egress are separate facts: access acceptance does not make metering available.
