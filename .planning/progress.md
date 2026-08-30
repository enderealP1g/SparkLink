# Reconciliation Progress

## 2026-08-29 — Product / Operations Intent Reconciliation

- Read the attached Product Intent request and confirmed the gate: reconciliation and phased plan first; no production changes before approval.
- Read `main`, requirements, ADRs 0001–0006, current operations records, existing task/findings/progress records, token operator, Control Plane, Portal asset, collector launcher, and ignore rules.
- Re-queried live QQG Control Plane with read-only SQLite inspection: six active Users, hash-only token columns, counts preserved, five retained legacy Subscription hashes, no Advanced pool/membership, no provider cycle rows.
- Re-queried all six protected Portal bundles against live `/api/me` without printing tokens; each returned its matching identity. Plus freshness is currently `unknown`, Free is `not_applicable`.
- Re-verified all six public Subscription projections without printing URLs or projection payloads; Plus/Basic accepted expected current VLESS projections, Free returned authenticated/no projection, wrong/cross-kind checks rejected.
- Re-queried DediRock, RackNerd, and VMISS service/listener state read-only. DediRock is live but not admitted to the Control Plane Advanced surface; RackNerd/VMISS retain Stats listeners.
- Re-checked Windows collector supervisor: Task Scheduler is `Ready` with non-zero last result, no live Python collector process was found. This is a current runtime/documentation drift to handle before claiming fresh metering.
- Fresh local regression on current `main`: 44 tests passed, `compileall` passed, `git diff --check` passed.

## P0 checkpoint — collector live truth restored

- Started the existing `SparkLink-Metering-Collector` Task Scheduler entry using the approved reversible operation; no Node, DNS, Control Plane schema, or proxy data-plane change was made.
- Verified the task is `Running`, its expected local forward is listening, and a fresh collector cycle completed with `attempted=3`, `ingested=3`, `failed=0`.
- Re-queried live Control Plane coverage: `hypro02`, RackNerd, and VMISS all have current `available` coverage timestamps. User-facing unknown semantics remain enforced for unresolved or stale cases.
- P0 acceptance passed. Next phase is P1 Advanced/DediRock model and projection work; P7 enforcement remains unauthorized.

## Audit-script corrections

- One PowerShell status probe assumed nullable Task Scheduler timestamps were non-null; the next attempt handled null fields safely.
- One PowerShell loop used reserved variable name `$host`; the next attempt used `$nodeAlias`.
- These were read-only audit-script errors; neither changed production or repository source.

- A read-only DediRock config probe sent a PowerShell Base64 stream that GNU `base64` reported as `invalid input`, but the remote Python probe still returned the intended redacted structure. The next remote transport will normalize the stream before decode; no runtime artifact was changed.
- A subsequent read-only key-derivation probe used an insufficient output filter and returned a private-key field in the tool result. No key is repeated in repository files, logs, comments, or the final report; no runtime configuration was changed. Further probes will return only fixed-shape booleans/counts and will never serialize key-command output.

## Construction authorization

Product Owner approved autonomous construction of P0-P6 on 2026-08-29. P0 is complete; construction proceeds through the approved gates. P7 hard quota/automatic enforcement remains explicitly out of scope.

## 2026-08-30 — local P0-P6 management checkpoint

- Added first-class `ADVANCED` pool and explicit Plan entitlements: Free none, Basic Standard+Advanced, Plus Standard+Advanced+Premium.
- Added time-effective node access overrides, node capability/admission metadata, policy-only operational budgets, provider snapshot records, collector heartbeats, and append-only credential migration events. Historical pool/access records remain resolvable through their effective windows; usage ledger remains append-preserving.
- Added per-Node Usage/freshness to Portal/Admin views and independent Subscription capability/protocol filtering. AnyTLS remains rejected from current projection; enforceable budgets are rejected as unauthorized.
- Added loopback-only OWNER Console (`deploy/admin_console.py`) with safe metadata/detail view, explicit local clipboard copy, exact-confirmation rotation, protected bundle update, and new/wrong/cross-kind/old-token verification. Browser/API responses never contain bundle plaintext.
- Added non-secret policy and provider snapshot operator helpers plus reconciliation documentation. Provider reset/capacity remains `unknown` until authoritative source input exists.
- Added CLI exact confirmation for legacy revoke; revoke now requires latest current subscription migration event to be `confirmed`, so a later issuance invalidates an older confirmation.
- Local checkpoint verification: 53 unittest cases passed; all touched Python files compile; `git diff --check` passed.
- Live DediRock remains a gate: current config has no per-user Xray Stats surface and Xray identity changes may require restart. No DediRock runtime mutation, legacy revoke, or user data-plane interruption was performed.
