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

## 2026-08-30 — live P2-P6 management checkpoint

- Deployed the approved management-plane slice to QQG with a protected backup and rollback path; Control Plane health is `ok`, six new management tables are present, hash-only user credentials and pre/post migration counts are clean, and proxy service states are unchanged.
- Applied the approved effective access and policy-only budget metadata: Hegin VMISS primary/QQG available; root QQG primary/VMISS reserved; abing VMISS deny/QQG primary with 200 GB policy; root/Hegin/abing/dangbin DediRock Advanced allowances are 700 GB policy-only. No hard quota, runtime DediRock admission, or legacy revoke was performed.
- Reconciled all six protected delivery bundles using trusted-bundle reuse where available and issue-only where plaintext was unavailable. Public subscription checks, wrong/cross-kind rejection checks, and per-user `/api/me` self-scope checks passed without printing secrets. OWNER index contains metadata only.
- Live projection is truthful for the currently admitted runtime: root/Hegin 6 VLESS entries across Standard+Premium, abing 4 across Standard+Premium with VMISS denied, dangbin 2 Standard, and Free not configured. Advanced remains an entitled but unadmitted runtime surface; AnyTLS is absent.
- Collector recovery required replacing a stale task run after its outer SSH tunnel exited. A foreground smoke cycle then completed 3/3; the formal Scheduled Task was restarted and verified `Running` with local tunnel listening, 3/3 safe collector cycle, and live `completed` heartbeat.
- OWNER Console smoke passed on a separate local forward: loopback page and authenticated metadata state both returned 200 for all six Users; the response contains no exact secret-bearing fields. No rotate or clipboard operation was invoked.
- Portal landing page acceptance is complete through the real public browser: the page rendered root self data as OWNER/Plus with the expected Customer Cycle and independent Standard/Advanced/Premium rows; no token was entered into chat or emitted in output.

## 2026-08-30 — DediRock / Advanced admission authorized

- Product Owner explicitly authorized DediRock production admission, including a reversible runtime reload/restart, while retaining the independent Access / Metering / Quota boundaries. P7 hard quota and automatic blocking remain unauthorized.
- Current phase is read-only DediRock discovery and admission design. Do not write DediRock config, x-ui rows, or Control Plane membership until the protected rollback point and exact managed identity mapping are verified.

## 2026-08-30 — DediRock / Advanced admission completed

- Deployed the atomic runtime-admission endpoint to QQG with a protected code backup at `/var/backups/sparklink-control-plane/runtime-admission-20260830T044354Z/sparklink_control_plane.py`; the live source SHA matched the tested candidate and remained `sparklink:sparklink 0640` with service/health active.
- DediRock discovery found all four expected stable managed Reality identities already present. A protected DediRock baseline config backup was retained at `/var/backups/sparklink-identity-migration/admission-baseline-20260830T043755Z-dedirock/xray-config.json`; no runtime mutation, token rotation, or Xray restart was needed for admission.
- Isolated client acceptance passed 4/4 through temporary Xray clients and real public HTTPS requests. Control Plane admission is idempotent: four managed credentials and four Advanced projections were registered, then the rerun reused all eight records and corrected the node display name to `DediRock Advanced serving Node` without creating duplicate membership.
- Refreshed all six protected delivery bundles without rotating any User token. Public projection checks passed for the four eligible Users (7/7/5/3 entries as applicable), root OWNER self-scope passed, and Free Users remained not configured. Legacy/shared access was unchanged.
- Restarted the Windows collector task so it loaded the four-node config. Formal collector evidence is `attempted=4`, `ingested=3`, `unknown=1`, `failed=0`; DediRock coverage and Advanced Usage remain `Unknown`, with no synthetic zero or aggregate attribution.

## 2026-08-30 — Provider telemetry adapter checkpoint

- Added `src/sparklink_provider_telemetry.py`, `deploy/collect_provider_snapshots.py`, strict single-snapshot validation, provider adapter registry, non-secret example schema, and provider operations documentation.
- Live `--dry-run` resolved the four existing resources. Live collection appended four `unknown` snapshots (`4 recorded / 0 failed`) because no authorized official API, stable endpoint, or dashboard export is configured. No proxy/runtime mutation occurred; provider reset/capacity/usage remain unknown.
- Admin Console now shows each inventory resource even when its latest snapshot is missing/unknown, including source and observed time, and shows per-pool/per-node usage plus per-user delivery availability in the main OWNER table.
- Deployed the final Control Plane snapshot validation to QQG with protected backup `/var/backups/sparklink-control-plane/provider-telemetry-20260830T051442Z`; live source SHA matched the local candidate, target remained `sparklink:sparklink 0640`, health stayed `ok`, and Xray/Nginx/WireProxy remained active.

## 2026-08-30 — subscription display naming checkpoint

- Added canonical shared node display naming for current personal VLESS projections: `Pro-LA-01/02-{HyTru|Origin}-Direct-Reality`, `Standard-NY-{HyTru|Origin}-Direct-Reality`, and the common `SparkLink-DediRock-Advanced`; existing VeilShift labels remain unchanged.
- Added the Admin-only fragment update endpoint and `deploy/standardize_subscription_names.py preview|apply`. The operation is entry-id based, transactional, rejects per-User alias collisions, and changes no URI core field or legacy projection row.
- Live preview found 22 current projected entries across six Users and 16 old aliases. The Admin-safe second pass included abing's two current-but-denied VMISS entries; final reconciliation covered all 24 current VLESS entries. Fresh preview found zero remaining changes. Fresh public verification passed root/Hegin/abing/dangbin at 7/7/5/3 entries, while Free Users remained `503 / not_configured`.
- Root OWNER acceptance remained valid: Plus, OWNER, `legacy-pre-baseline`, Standard/Advanced/Premium, and self-scoped `/api/me`. All six Portal credentials still authenticated; no token rotation, legacy revoke, Node runtime change, or Usage mutation occurred.
- Control Plane deployment used rollback `/var/backups/sparklink-control-plane/subscription-naming-20260830T061930Z`; service health recovered after the probe script itself was corrected. The final candidate passed local regression, syntax, and URI fragment-only tests.
