# SparkLink Product / Operations Intent Reconciliation

Status: in_progress (P0-P6 management slice complete; authoritative provider telemetry remains pending)

## Goal

Reconcile the current `main` repository and live SparkLink runtime against the Product Owner intent in the attached reconciliation request. Preserve the working Production MVP, token isolation, metering history, data-plane independence, and truthful `Unknown` semantics while identifying the smallest coherent implementation slices that make OWNER operations direct and reliable.

## Current phase

- [x] R0 — Read current `main`, requirements, ADRs, operations records, implementation entry points, and current protected runtime inventory.
- [x] R1 — Re-check live Control Plane, six-user metadata, database shape/counts, public health/projection, Node services, and Windows collector state.
- [x] R2 — Produce AS-IS vs Product Intent reconciliation, blockers, unknowns, phased plan, and approval gates.
- [x] R3 — Product Owner approval of the plan and first implementation slice.

## Proposed implementation phases after approval

### P0 — Restore live operational truth before feature admission — complete

- Resolve the live mismatch between the collector documentation and Task Scheduler/process state. Repair or re-register only after an approved, reversible operator change.
- Independently verify one complete collector interval, freshness timestamps, coverage states, and proxy data-plane independence.
- Update the operations record with current evidence; do not mark coverage available when the collector is stopped or stale.

Checkpoint: the existing Windows task was started with the approved reversible action. A fresh interval completed with 3/3 Node ingest and 0 failed; latest live coverage timestamps advanced. No proxy data-plane configuration changed.

### P1 — Restore Advanced as a truthful access/subscription vertical slice — complete

- Add `ADVANCED` as a first-class Pool without introducing username-specific Plans.
- Perform a separate DediRock admission check: Xray/VLESS service acceptance, per-user managed credential mapping, protocol and rollback evidence.
- Add DediRock membership and the four intended Advanced entitlements only after admission evidence; allow Subscription delivery while marking metering `Unknown` if no per-user counter exists.
- Keep DediRock hard quota enforcement disabled until reliable per-user metering exists. Do not add AnyTLS to the projection.
- Acceptance: Basic and Plus projections contain their intended Standard/Advanced/Premium surfaces; Free remains unchanged; no usage is fabricated.

Implementation checkpoint: `ADVANCED` schema/pool, VLESS-only capability metadata, effective access, subscription filtering, and policy-only budget are implemented. DediRock was admitted to the formal Advanced surface after read-only discovery, protected baseline backup, managed identity mapping, Xray config validation, and 4/4 isolated client acceptance. The existing stable managed identities were already present, so no DediRock runtime rotate/restart was needed in the admission run.

Admission sequence completed: read-only discovery → protected rollback point → isolated per-user identity plan → config/persistent-row validation → service/client acceptance → Control Plane membership/projection → collector/coverage evidence. Runtime apply remains available for future missing identities with config SHA protection and automatic rollback.

### P2 — Represent effective access and operational allowances — implemented and live-verified

- Add generic, time-effective User-specific access decisions/allocations layered after Plan defaults: allow/deny plus Primary/Backup/Available/Reserved semantics where applicable.
- Add a separate operational-budget record keyed to User/Node/Provider Resource Cycle, with explicit `policy_only` versus enforceable state.
- Seed the Product Owner's current intended Premium allocation only after review: Hegin VMISS primary / QQG available; root VMISS reserved / QQG primary; abing VMISS deny / QQG primary with a 200 GB policy budget.
- Keep historical Usage append-preserving and never hardcode a username-specific Plan.

### P3 — Re-establish metering freshness and per-Node User Usage — implemented and live-verified

- Extend the verified collector path and expose freshness/heartbeat so `Ready`/stopped is visible rather than silently treated as healthy.
- Keep Xray Stats as the current authoritative customer observation surface. RackNerd/VMISS/QQG collection remains live; DediRock is explicitly configured as `Unknown` because no per-user Stats source is available.
- Extend User/Admin views to show User × Node × Pool × Customer Cycle, with `0`, `Unknown`, and `stale` distinct.
- Keep one Usage Ledger with Customer Cycle and Provider Resource Cycle as separate query windows.

### P4 — Build the smallest OWNER read-oriented Admin Console — implemented; live management path verified

- Add an OWNER-only read surface for health, attention items, Users, Plan/Role, default and effective entitlements, per-Node/per-Pool Usage, coverage freshness, subscription state, credential/migration state, and infrastructure context.
- Make the homepage answer service health, Premium capacity pressure, and items requiring OWNER attention.
- Do not iframe vendor dashboards; read normalized local snapshots and show source/observed_at/freshness.
- Keep high-risk operations behind explicit confirmation; keep dangerous schema/runtime/provider changes outside normal Admin UX.

### P5 — Make credential and legacy migration state durable and operator-friendly — implemented and six-user delivery reconciled

- Preserve the current hash-only Control Plane boundary. If direct reveal/copy is needed, use an explicit Windows DPAPI-protected operator vault/bundle with plaintext only in memory during copy/reveal; never move plaintext into SQLite, Git, logs, or chat.
- Add append-only migration events/state: issued, delivered, fetched, managed traffic observed, confirmed, legacy retirement ready, retired. Keep legacy Subscription retirement distinct from runtime Credential retirement.
- Let OWNER perform routine view/copy/inspect directly; require confirmation for rotate/revoke/disable/allowance changes.
- Retire legacy/shared credentials only after explicit migration confirmation and a fresh verification of the replacement path.

### P6 — Add provider resource adapters and capacity snapshots — implemented; authoritative data pending

- Prefer official provider APIs, then documented/stable endpoints, with source and freshness recorded; browser/dashboard automation remains fallback only.
- Normalize capacity, used, remaining, provider reset window, financial cycle, next due, observed_at, and coverage/status into the management plane.
- Keep provider totals for capacity/allocation decisions only; never attribute them to User Usage or use them as a quota substitute.
- Do not let provider adapter failure affect proxy forwarding.

Implementation checkpoint: four provider adapters (RackNerd, VMISS, QQGNet, DediRock) now share a strict normalized snapshot contract and source priority. `deploy/collect_provider_snapshots.py` reads the local resource inventory, accepts only non-secret trusted exports, and records explicit `unknown` snapshots when no authorized source is configured. The live run recorded four `unknown / 4 recorded / 0 failed` management snapshots; no runtime or data-plane change occurred. Admin Infrastructure reads the latest local snapshot and does not synchronously call providers.

Implementation checkpoint: current personal VLESS projection aliases now follow the shared user-facing naming system (`Pro-LA-01/02`, `Standard-NY`, and common `SparkLink-DediRock-Advanced` forms). VeilShift labels are preserved. An Admin-only fragment update workflow is transactional, entry-id scoped, and verifies that URI core fields remain unchanged.

### P7 — Quota/enforcement only after coverage is proven

- Treat the 700 GB DediRock policy and abing's 200 GB QQG policy as non-enforcing operational policy until the corresponding User/Node counters and Provider Resource Cycle are reliable.
- Design hard quota/blocking/throttling as a later, explicitly approved slice with counter-reset, stale, failure, and rollback semantics.

## Approval and safety gates

- No production mutation before Product Owner approves this reconciliation and the selected phase.
- Before schema/data migration: read-only dry run, protected database backup, explicit migration/rollback procedure, schema/FK/count verification, and fresh API acceptance.
- Before DediRock admission: service/config/listener checks, managed credential mapping evidence, subscription projection check, and explicit decision on `Unknown` metering.
- Before allocation/allowance changes: effective timestamps, audit reason/source, projected before/after access, and no historical Usage rewrite.
- Before legacy retirement or token revocation: explicit OWNER confirmation, replacement fetch/traffic evidence, and independent old-token rejection check.
- Secret-bearing material remains outside Git/logs/chat; public endpoints must not make Control Plane admin credentials or private delivery paths a data-plane dependency.

## Success criteria

- OWNER can inspect all six Users and the effective access decision without SSH, SQLite, Git, or Codex intervention.
- Basic/Plus/Free projections match the approved entitlement model, including Advanced, while AnyTLS remains deferred.
- Effective per-User Node allocation and operational policy are visible and time-effective.
- Per-Node/per-Pool Usage and freshness preserve `Unknown` versus actual zero and never use provider totals as User Usage.
- Provider snapshots are source-labelled, freshness-labelled, and isolated from the proxy data plane.
- Credential delivery/migration is auditable, individually isolated, and never stores plaintext in the Control Plane database.
- Every high-risk operation has explicit confirmation and rollback evidence.

## Explicit assumptions and limits

- The attached Product Intent is the business semantic input; live repository/runtime evidence is authoritative for current implementation state.
- Provider dashboard values included in the request are not treated as verified current evidence until an authoritative provider source is read and recorded.
- `hypro02`/QQG remains `PREMIUM / CONDITIONAL` until new evidence changes that qualification.
- AnyTLS, payment, billing automation, scheduler/orchestration, Clash, automatic routing, and cosmetic redesign remain deferred unless directly required by an approved slice.
