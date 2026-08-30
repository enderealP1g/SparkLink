# 2026-08-30 DediRock dual Origin/native and HyTru egress

This record documents the Production Operations correction that makes DediRock
Advanced expose two independent user-facing route variants. It does not add a
Plan, change entitlement policy, enable quota enforcement, or retire legacy
access.

## Result

| Item | Verified state |
| --- | --- |
| Ingress | Direct `dedirock.enrpiglink.top:443` VLESS + REALITY |
| Native route | `direct` / `freedom`, displayed as `Advanced-LA-Origin-Direct-Reality` |
| HyTru route | `warp` / WireGuard, displayed as `Advanced-LA-HyTru-Direct-Reality` |
| Managed runtime identities | 8 total: 4 existing HyTru + 4 separately identified Origin |
| Isolated acceptance | 8/8: Origin `warp=off`, HyTru `warp=on` |
| DediRock metering | `Unknown`; no per-user Stats source available |
| Quota | `unavailable`; 700GB remains `policy_only` |
| Legacy/shared access | Unchanged |

The existing `sparklink:<user>:advanced` identity remains the HyTru path. The
new `sparklink:<user>:advanced:origin` identity has its own UUID and Control
Plane runtime reference hash and is routed to `direct`. The two identities are
never combined behind one display alias.

## Current projection evidence

| User | Plan | Public entries | DediRock pair | Other policy |
| --- | --- | ---: | --- | --- |
| root | Plus | 8 | Origin + HyTru | VMISS reserved, QQG primary |
| Hegin | Plus | 8 | Origin + HyTru | VMISS primary, QQG available |
| abing | Plus | 6 | Origin + HyTru | VMISS denied, QQG primary |
| dangbin | Basic | 4 | Origin + HyTru | No Premium |
| liuwen | Free | 0 / not configured | Not entitled | unchanged |
| zhanhao | Free | 0 / not configured | Not entitled | unchanged |

The public `sub.enrpiglink.top` projection was fetched and compared against
the Admin-safe current entry metadata. Each currently allowed node family has
both route aliases. The abing/VMISS deny policy is intentionally not bypassed.
AnyTLS remains absent.

## Operator workflow

From the Windows control-machine repository root:

```powershell
python deploy\ensure_dedirock_dual_routes.py preview
python deploy\ensure_dedirock_dual_routes.py apply
python deploy\standardize_subscription_names.py preview
python deploy\issue_user_tokens.py reconcile
```

`apply` performs read-only discovery, an SHA-guarded runtime mutation, a
root-only remote backup, Xray validation, a DediRock-only restart, and 8
isolated public checks. If runtime apply or acceptance fails, the remote
configuration is restored automatically. Control Plane admission is idempotent
and creates/reuses only the new Origin mappings; Portal/Subscription tokens are
not rotated by this operation.

The six delivery bundles remain in the ignored, ACL-protected
`runtime\delivery\<username>\delivery.json` location. No token, UUID, key, or
complete credential-bearing URI is stored in this document or emitted by the
operator output.

## Boundary

Access capability, metering capability, and quota-enforcement capability remain
independent. DediRock access is admitted for eligible Basic/Plus Users even
though Usage is `Unknown`; provider/host/Nginx/WARP aggregate traffic is never
used as User Usage, and no automatic blocking is enabled.
