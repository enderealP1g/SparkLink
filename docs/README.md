# Documentation Map

This directory contains the traceable project documentation for SparkLink.

## Areas

- `requirements/` — baseline requirements and iteration specifications.
- `architecture/` — system boundaries, reviewed As-Is context, Candidate Proposal and the bounded Production MVP Architecture.
- `decisions/` — accepted project-level decisions and their rationale.
- `operations/` — dated operational evidence, rollback points and observability guidance.

The current requirements baseline is [Training Iteration #1: User Identity + Usage Metering](requirements/ITERATION_01_USAGE_METERING.md). The Product Owner's current implementation-scope reconciliation is documented in [Production MVP reconciliation](requirements/ITERATION_01_PRODUCTION_MVP_RECONCILIATION.md), and the bounded implementation decision is [ADR-0004](decisions/0004-production-mvp-vertical-slice.md).

No credentials, UUIDs, subscription tokens, private keys, passwords, or other secrets belong in this documentation tree.

本次 MVP 的 QQG origin、public edge 与 metering operations evidence 记录在 [Production MVP Control Plane Operations Record](operations/2026-08-29_PRODUCTION_MVP_CONTROL_PLANE.md)；hardening evidence 见 [Metering hardening record](operations/2026-08-29_METERING_HARDENING.md)。
Production identity 与 customer/provider cycle reconciliation 见 [identity and cycle record](operations/2026-08-29_PRODUCTION_IDENTITY_CYCLE_RECONCILIATION.md) 与 [ADR-0006](decisions/0006-production-identity-and-customer-cycle.md)。
当前 P0→P6 Product / Operations reconciliation 见 [Product Operations reconciliation](operations/2026-08-29_PRODUCT_OPERATIONS_RECONCILIATION.md)。
