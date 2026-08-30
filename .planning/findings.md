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

## Earlier no-change boundary observed

- No Node configuration, DNS, Cloudflare route, Task Scheduler registration, Control Plane database, token, subscription, or credential was changed during this reconciliation.
- The only local additions from this review are the `.planning/` review artifacts.

## 2026-08-30 — DediRock admission discovery

- Read-only discovery through the existing `dedirock-admin` operator path confirmed `xray`, `nginx`, and `sing-box` are active. The Xray unit uses `/etc/xray/config.json` with the installed Xray binary and `run -test -config` validation shape; the config is root-owned with mode `0640`.
- The DediRock config contains a public `vless` `reality` inbound on TCP/443 with three existing clients and a loopback `vless` XHTTP bridge on TCP/10080 with three clients. Existing client identity values and Reality key material were not recorded in the findings.
- DediRock has no `/etc/x-ui/x-ui.db`. The Xray Stats query against `127.0.0.1:62789` returned non-zero, so admission can prove access but must record metering as `unknown`; no host, Nginx, or provider aggregate may be attributed to a User.
- Active listeners include 443, 10080, 2053, 8443, 9443, and 40000. The intended admission path is the direct `dedirock.enrpiglink.top:443` Reality endpoint; the existing XHTTP/ShadowTLS/AnyTLS paths remain outside the Advanced VLESS projection.
- The admission runner must use a stable managed email per SparkLink User, clone only a non-managed Reality client template, preserve all old clients, generate fresh UUIDs only for missing managed identities, validate before restart, retain a root-only remote backup, and return only counts/hashes/status metadata.

## 2026-08-30 — DediRock Advanced admission checkpoint

- DediRock formal admission passed for `root`, `Hegin`, `abing`, and `dangbin`: the live Reality/443 path had all four expected stable managed identities already present, so no runtime credential rotation or Xray restart was necessary. A root-only baseline config backup was nevertheless retained at `/var/backups/sparklink-identity-migration/admission-baseline-20260830T043755Z-dedirock/xray-config.json` with SHA-256 `25343c73ed4796c91229ff4a34554fd9f23cd7d8215ac3d1d991ef86218d8cc0`.
- Four isolated transient Xray client configurations completed real SOCKS-to-public-request acceptance. DediRock is now `active/verified`, an `ADVANCED` member, and advertises only VLESS access/subscription; XHTTP, ShadowTLS, and AnyTLS remain outside the projection.
- Control Plane now has four managed DediRock credentials and four current Advanced subscription entries. Public personal projections are root/Hegin 7 entries, abing 5 entries, dangbin 3 entries; Free Users remain not configured. No legacy/shared access was revoked.
- DediRock capability is `access=allowed`, `subscription=allowed`, `metering=unknown`, `quota=unavailable`. Collector coverage is intentionally `unknown`, and the formal Windows heartbeat is `degraded` with `attempted=4`, `ingested=3`, `failed=0`, `unknown=1`; no provider/host/Nginx aggregate is used as User Usage.

## 2026-08-30 — Provider telemetry adapter checkpoint

- Live Control Plane inventory contains four Infrastructure Resources: DediRock, QQGNet, RackNerd, and VMISS. No authorized provider API credential, documented stable telemetry endpoint, or local dashboard export was present in the current operator context; provider snapshot table was empty before this checkpoint.
- Added a strict adapter registry and normalized snapshot contract with source priority `official_api` → `stable_endpoint` → `dashboard_export`. The contract rejects secret-like extra fields, incomplete `available` byte values, inconsistent `capacity = used + remaining`, and telemetry values attached to `unknown`/`unavailable` status.
- A live `--dry-run` resolved all 4 resources. The live collector then appended 4 source-labelled `unknown` snapshots with `4 recorded / 0 failed`; capacity, used, remaining, reset, and next due are all null. Contract/inventory transfer and due fields were not promoted to telemetry.
- Admin Infrastructure now reads the latest stored local snapshot and displays provider/resource/source/observed/freshness metadata. It does not synchronously call provider dashboards, and provider collection failures cannot affect the proxy data plane or Customer Usage.

## 2026-08-30 — subscription display naming evidence

- The live root projection already contained the requested canonical `Pro-LA-02-HyTru-Direct-Reality` form. Hegin, abing, and dangbin still had legacy plan-prefixed Xray/REALITY remarks; the public entry order aligned with safe Admin `entry_id` metadata.
- The current live mapping is stable by Node and old route suffix: QQG `hypro02` is LA-02, VMISS is LA-01, RackNerd is Standard-NY, and odd/even legacy suffixes preserve HyTru/Origin semantics. DediRock's user-specific display suffix was removed to make its Advanced remark shared across Users.
- Only current enabled VLESS entries were eligible. The public pass covered 22 accessible entries; a second Admin-safe pass also renamed abing's two current-but-denied VMISS entries. Legacy/shared subscription entries remained untouched, and no VeilShift entry was changed. The Admin endpoint rejects non-current, disabled, non-VLESS, unknown, malformed, and colliding alias requests.
- Independent public projection verification confirmed that the new names are presentation-only: every entry's URI core (including identity and query parameters) matched the pre-apply snapshot. No credential, Plan, Pool, access, metering, quota, or runtime state changed.

## 2026-08-30 — DediRock HyTru route repair and naming decision

- A fresh read-only probe of `dedirock-admin` found four managed Advanced
  client identities in the live Reality inbound. Their email labels are
  `sparklink:<user>:advanced`; no credential values were recorded.
- Before repair, all four isolated managed-client checks returned `warp=off`:
  the live `warp` WireGuard outbound existed, but no exact managed-user route
  rule selected it. A route repair added one exact-user `outboundTag=warp`
  rule, after an SHA-guarded root-only backup and Xray config validation.
- Four post-repair isolated public checks returned `warp=on`; a second apply
  was idempotent and reported `changed=false`. The current DediRock display
  alias is `Advanced-LA-HyTru-Direct-Reality`; the old Origin and
  user-specific forms are migration inputs only. The repair did not add
  entries, rotate credentials, revoke legacy access, or change metering/quota
  semantics.
- The protected rollback artifact is
  `/var/backups/sparklink-identity-migration/20260830T070546Z-dedirock-hytru-route/xray-config.json`
  (`root:root`, file `0600`, parent directory `0700`). A before/after
  structural comparison found only the routing rule changed; Xray, Nginx, and
  sing-box remained active.

## 2026-08-30 — dual egress reconciliation

- Product Owner clarified that every eligible user-facing node must expose two
  distinct route variants: `Origin(native)` and `HyTru`; the already-fixed
  VeilShift naming/semantics remain unchanged.
- Current Control Plane projections already contain both variants for the
  existing QQG/VMISS/RackNerd VLESS node families. DediRock is the exception:
  each of the four eligible users currently has exactly one `ADVANCED` entry,
  named `Advanced-LA-HyTru-Direct-Reality`.
- Fresh DediRock inspection confirmed both `direct` (`freedom`) and `warp`
  (`wireguard`) outbounds exist, but all four managed Reality identities
  (`sparklink:<user>:advanced`) are selected by one exact `warp` route rule.
  Therefore the missing `Advanced-Origin` item is a real runtime/projection
  gap, not a v2rayN refresh problem.
- The safe extension is to preserve the existing identity as the HyTru path,
  add one separately identified managed Origin identity per eligible user,
  route those identities to `direct`, and add one matching Control Plane
  credential/subscription entry per user. Existing legacy/shared access and
  current HyTru entries must remain untouched.
- Free users remain outside the Advanced entitlement. The `abing` VMISS deny
  policy remains in force; dual-route coverage means every currently allowed
  node for each eligible Plan/user, not an override of access policy.
