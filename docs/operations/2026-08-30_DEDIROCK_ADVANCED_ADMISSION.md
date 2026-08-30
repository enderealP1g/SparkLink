# 2026-08-30 DediRock Advanced admission

本记录描述 DediRock 进入 BASIC/PLUS personal Subscription 的 Production Operations admission。它不启用 P7 hard quota/automatic blocking，不撤销尚未确认迁移的 legacy/shared access，也不把 provider/host/Nginx aggregate 当作 User Usage。

## Admission result

| Item | Evidence |
| --- | --- |
| Runtime path | Direct `dedirock.enrpiglink.top:443` Xray VLESS Reality |
| Managed Users | `root`, `Hegin`, `abing`, `dangbin` |
| Runtime identity action | Four existing stable managed identities reused; no unnecessary rotate/restart |
| Isolated client acceptance | 4/4 real transient Xray clients completed public HTTPS request |
| Control Plane | DediRock `active/verified`, `ADVANCED`, VLESS access/subscription allowed |
| Metering | `Unknown`; no per-user Stats source is available |
| Quota | `unavailable`; 700GB remains policy-only |
| Legacy/shared access | Unchanged |

The admission runner is idempotent. Its first successful Control Plane registration created four managed credentials and four current Advanced projection entries; a follow-up run reused all eight records and did not create duplicate active membership. Runtime identities are keyed by stable managed Xray email references; the Control Plane stores only the corresponding reference hashes.

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
| root | Plus | 7 | present | Unknown |
| Hegin | Plus | 7 | present | Unknown |
| abing | Plus | 5 | present | Unknown |
| dangbin | Basic | 3 | present | Unknown |
| liuwen | Free | 0 / not configured | absent | Not applicable |
| zhanhao | Free | 0 / not configured | absent | Not applicable |

The Windows collector uses `metering_mode=unknown` for DediRock. Live evidence is `attempted=4`, `ingested=3`, `unknown=1`, `failed=0`; the formal heartbeat is `degraded` only because one configured Node has no per-user source. DediRock remains visible in per-Node access and Usage views as `Unknown`, never `0`.

## Operator workflow

From the repository root on the Windows control machine:

```powershell
python deploy\admit_dedirock.py
python deploy\issue_user_tokens.py reconcile
python deploy\issue_user_tokens.py copy --user Hegin --kind portal
python deploy\issue_user_tokens.py copy --user Hegin --kind subscription
python deploy\admin_console.py
```

The admission command emits only safe counts/status/path metadata. The six user delivery bundles remain in ignored, ACL-protected `runtime\delivery\<username>\delivery.json`; the OWNER index lists metadata only. DediRock runtime URIs, UUIDs, key values, Portal tokens, and Subscription tokens are never printed or stored in this document.

## Deferred boundary

DediRock XHTTP/Cloudflare, ShadowTLS, and AnyTLS paths remain outside the Advanced VLESS projection. Reliable per-user DediRock metering is still an open provider/runtime evidence task; no hard quota, automatic blocking, or synthetic attribution is allowed.
