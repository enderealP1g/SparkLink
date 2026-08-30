# Subscription Node Display Naming — 2026-08-30

## Scope

This checkpoint standardizes the user-facing VLESS URI fragment, which is the
node remark displayed by clients such as v2rayN. It does not rename a Control
Plane Node identity and does not change an endpoint, UUID, Reality parameter,
route, Pool, credential, or Usage association.

The naming rule is shared by `root` and all other Users. Existing VeilShift
labels are preserved exactly as an exception because that label is already
defined by Product Intent.

## Canonical names

| Control Plane Node | Canonical display names |
| --- | --- |
| `hypro02` / QQG LA-02 | `Pro-LA-02-HyTru-Direct-Reality`, `Pro-LA-02-Origin-Direct-Reality` |
| `vmiss` / LA-01 | `Pro-LA-01-HyTru-Direct-Reality`, `Pro-LA-01-Origin-Direct-Reality` |
| `racknerd` / NY Standard | `Standard-NY-HyTru-Direct-Reality`, `Standard-NY-Origin-Direct-Reality` |
| `dedirock` / Advanced | `SparkLink-DediRock-Advanced` |
| VeilShift | Existing label unchanged |

The old `Plus|Basic-LA|NY-Xray-VLESS-REALITY-N` labels use the established
odd/even route convention: odd suffixes map to `HyTru`, even suffixes map to
`Origin`. An unrecognized label or Node fails closed instead of being guessed.

## Implementation

- `src/sparklink_subscription_naming.py` owns the canonical mapping and
  fragment-only URI rewrite guard.
- `POST /api/admin/subscription-aliases` is Admin-only and accepts explicit
  `entry_id` plus alias pairs. It updates only current, enabled VLESS entries
  in one transaction, rejects duplicate aliases per User, and never returns a
  URI or credential.
- `deploy/standardize_subscription_names.py preview` builds a non-mutating
  plan from the protected local bundles, the public
  `sub.enrpiglink.top` projection, and safe Admin entry metadata.
- `deploy/standardize_subscription_names.py apply` applies the plan and
  independently verifies every public entry. If verification fails after an
  apply, it submits the in-memory old aliases as a rollback.
- Retained legacy subscription rows are not changed because they are not part
  of the current personal projection.

The regular operator command is:

```text
python deploy/standardize_subscription_names.py preview
python deploy/standardize_subscription_names.py apply
```

The command reads Portal credentials and Subscription URLs only from the
existing ACL-protected `runtime/delivery/<username>/delivery.json` files;
they remain in memory and are never printed.

## Live evidence

- The first public projection preview covered all six Users and 22 visible
  current VLESS entries; 16 old display fragments changed. A second Admin-safe
  reconciliation included the two current-but-denied abing/VMISS entries, so
  all 24 current VLESS entries are now canonical; legacy rows remain outside
  the operation.
- A fresh preview after the second apply reported `changed_entries=0`.
- A fresh process verified canonical names and public projection status for
  all 24 current entries and public status for root/Hegin/abing/dangbin at
  7/7/5/3 entries. Free liuwen/zhanhao remained `503 / not_configured` with
  zero entries.
- URI core comparisons passed for every entry: endpoint, identity, query and
  route parameters were unchanged; only the fragment differed.
- Root Portal acceptance still passed as `Plus` / `OWNER`, cycle
  `legacy-pre-baseline`, independent `STANDARD` / `ADVANCED` / `PREMIUM`
  pools, and self-scoped `/api/me` data. Wrong and cross-kind token checks
  remained rejected.
- The Control Plane was deployed with a protected rollback copy at
  `/var/backups/sparklink-control-plane/subscription-naming-20260830T061930Z`;
  service and `/healthz` were active after deployment. No proxy Node runtime
  was changed or restarted.

Plaintext token, UUID, key material, and full Subscription URI values are
intentionally absent from this document.
