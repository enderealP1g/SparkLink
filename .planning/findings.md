# Reconciliation Findings

Evidence date: 2026-08-29. Evidence sources were re-read from the checked-out `main` and re-queried from live runtime; no production mutation was performed during this review.

## Repository baseline

- `main` is at `4ae6cfb9491f3d42947eb36d12ad1aa152f8809e`, aligned with `origin/main` at the start of this review.
- The current commit contains the six-user protected delivery workflow, hash-only token migration, legacy Subscription grace hash, operator copy helper, and regression tests.
- `src/sparklink_control_plane.py` currently defines only `STANDARD` and `PREMIUM` in `POOL_NAMES`; the schema has no `ADVANCED` pool, User-specific Node allocation/override table, operational budget table, provider snapshot ingestion, or durable credential migration event state.
- `web/index.html` is a customer Portal only. There is no Admin Console UI. Current Admin routes are JSON overview/user metadata, token issuance/revoke, coverage, ingest, and manual subscription-entry operations.
- `user_view` currently returns Pool totals/coverage but not a per-Node Usage breakdown. Subscription URL is intentionally absent from the hash-only Portal response and is delivered out-of-band.

## Live Control Plane evidence

- QQG Control Plane service is active and loopback health is `ok`; public Portal `/healthz` is HTTP 200.
- Live `users` has six active Users: root/Plus/OWNER, Hegin/Plus, abing/Plus, dangbin/Basic, liuwen/Free, zhanhao/Free.
- Live schema contains `portal_token_hash`, `subscription_token_hash`, and `subscription_token_legacy_hash`; no plaintext token column is present. Database mode is `0600`; FK check is clean; no WAL/SHM residue was present.
- Live counts are preserved: users 6, billing cycles 12, credentials 28, subscription entries 26, observations 347, ledger rows 347. Five Users have a retained legacy Subscription hash; root does not.
- Live entitlements are only Plus→Standard/Premium for root/Hegin/abing and Basic→Standard for dangbin. There is no Advanced entitlement or DediRock membership.
- Live nodes: DediRock is `reference-only/unqualified`; `hypro02` is active/conditional Premium; RackNerd is active/verified Standard; VMISS is active/verified Premium. `provider_resource_cycles` is empty and all stored resource cycle statuses are `unknown`.
- Live subscription projections are current VLESS only: Plus Users have Standard + Premium entries, Basic has Standard entries, Free has no configured projection. AnyTLS is absent. DediRock is absent.
- Live current `/api/me` calls using the six protected local bundles returned the matching User identity and no cross-user identity exposure. Before the current customer-cycle baseline, the cycle key is `legacy-pre-baseline`. Plus Pool coverage currently reports `unknown` because current observations are not fresh; Free reports `not_applicable`.
- Fresh public projection verification passed for all six bundles: Plus and Basic authenticated successfully with expected current projection; Free authenticated but correctly returned no projection; wrong Subscription and Subscription-as-Portal checks were rejected.

## Live Node/runtime evidence

- DediRock currently has active Xray, Nginx, and sing-box listeners, including Xray/Nginx service paths, but no loopback Xray Stats listener was observed. This confirms a live data-plane capability but not a Control Plane admission or per-user metering capability.
- RackNerd currently has active x-ui and loopback Xray Stats listener. VMISS currently has active x-ui/sing-box and loopback Xray Stats listener. Control Plane is intentionally not installed on those Nodes.
- At the initial audit point Windows Task Scheduler entry `SparkLink-Metering-Collector` reported `Ready`, not running; its last result was non-zero and no actual Python collector process was found. After the approved reversible `Start-ScheduledTask` action, the task is `Running`, the tunnel is listening, and a fresh interval completed with 3/3 Node ingest and 0 failed. Latest live coverage timestamps advanced. The initial documentation/runtime mismatch is resolved for this checkpoint and should remain covered by a freshness/heartbeat signal.
- `sub.enrpiglink.top/healthz` returned HTTP 404; this is not by itself a failure because the subscription edge exposes tokenized delivery paths rather than a documented health endpoint. Actual per-user public projections passed.

## AS-IS vs Product Intent

### Already aligned

- Stable six-User identity and current Plan/Role values are live and consistent with the request.
- Customer Cycle is represented as Asia/Shanghai 15→15 with the 2026-09-15 baseline; legacy/pre-baseline history is retained.
- Portal and Subscription credentials are separate. Control Plane stores hashes only, and the Windows delivery path is protected/ignored.
- Per-user VLESS Subscription projection and public `sub.enrpiglink.top` delivery are operational for current configured Plans; AnyTLS is not in the projection.
- Xray observation/ledger code preserves append-only history and distinguishes missing/stale/unknown from actual zero. Proxy data plane is separate from Control Plane/metering.
- Legacy Subscription grace hashes provide a safe staged migration mechanism; blanket revocation is not required.

### Partially implemented

- OWNER credential delivery is functional through a local operator CLI and protected bundles, but routine operations still require knowing the repository/tool command and do not provide an OWNER Console. Durable delivery/fetch/confirmation state is missing.
- Admin JSON overview exposes users, nodes, resources, and cycle metadata, but not effective access, per-Node User Usage, migration state, current active URL/copy affordances, or resource capacity snapshots.
- Entitlement records exist but only express per-User pool defaults and allowance values; they cannot express the requested User-specific Node deny/primary/backup/reserved decisions.
- Infrastructure resource and provider-cycle schema exists, but no provider-cycle rows, provider snapshot source, observed capacity, remaining traffic, or freshness state is populated.
- Collector hardening exists in code, but the live Windows supervisor is not currently running; current Xray coverage therefore cannot be treated as continuously fresh.

### Direct conflicts

- The current domain has no `ADVANCED` pool. Product Intent requires Basic to include Standard + Advanced and Plus to include Basic's full entitlement plus Premium eligibility.
- DediRock is currently reference-only/unqualified and excluded from personal Subscription, while Product Intent says Advanced should be a normal daily-traffic carrier and access may be allowed even when metering is Unknown.
- Current Plus projections include both VMISS and QQG for abing; Product Intent says VMISS must be denied for abing while QQG remains primary.
- Current data model cannot represent Premium Primary/Available/Reserved/Deny allocation for Hegin/root/abing or the 200 GB abing operational policy.
- Current task/document state disagrees: the operations records and task plan say the collector is running, while live Task Scheduler/process evidence says it is stopped/not currently running.

### Missing

- Generic effective-entitlement resolver: Plan default → User override → effective Node access.
- Durable operational allowance/budget model separate from hard quota enforcement.
- Per-Node User Usage display and coverage/freshness heartbeat.
- OWNER read-oriented Admin Console and user detail view.
- Provider adapters/snapshots for VMISS, QQG, RackNerd, and DediRock.
- Durable credential migration state and explicit distinction between issued/delivered/fetched/confirmed/retirement-ready/retired.

## Current blockers and unknowns

1. Advanced/DediRock admission is the largest product-surface blocker. DediRock has live services but no current Control Plane pool membership, managed per-user projection, or reliable Xray per-user Stats surface.
2. Per-user Node usage coverage is incomplete. Current live API correctly says `unknown`; no provider total may be substituted.
3. The collector supervisor is not currently healthy/running despite stale documentation saying it is. Freshness cannot be considered operational until repaired and independently verified.
4. OWNER visibility remains CLI/JSON-oriented; there is no direct read-only Admin Console, effective access view, Provider Status view, or migration dashboard.
5. Provider cycle acquisition and authoritative reset evidence are unknown in the live database. The request's dashboard facts remain Product Intent input, not repository/live evidence.
6. Premium allocation and operational allowances are explicit Product Owner policy but have no durable representation; implementing them requires an effective-time/audit model, not username branches.
7. Legacy migration state is only a boolean-style retained hash plus local bundle metadata; it cannot prove fetched, traffic observed, or OWNER confirmation.

## No-change boundary observed

- No Node configuration, DNS, Cloudflare route, Task Scheduler registration, Control Plane database, token, subscription, or credential was changed during this reconciliation.
- The only local additions from this review are the `.planning/` review artifacts.
