# Documentation Map

This directory contains the traceable project documentation for SparkLink.

## Areas

- `requirements/` — baseline requirements and iteration specifications.
- `architecture/` — system boundaries, reviewed As-Is context, Candidate Proposal and the bounded Production MVP Architecture.
- `decisions/` — accepted project-level decisions and their rationale.
- `operations/` — dated operational evidence, rollback points and observability guidance.

The current requirements baseline is [Training Iteration #1: User Identity + Usage Metering](requirements/ITERATION_01_USAGE_METERING.md). The Product Owner's current implementation-scope reconciliation is documented in [Production MVP reconciliation](requirements/ITERATION_01_PRODUCTION_MVP_RECONCILIATION.md), and the bounded implementation decision is [ADR-0004](decisions/0004-production-mvp-vertical-slice.md).

No credentials, UUIDs, subscription tokens, private keys, passwords, or other secrets belong in this documentation tree.

本次 MVP 的 QQG origin deployment evidence 记录在 [Production MVP Control Plane Operations Record](operations/2026-08-29_PRODUCTION_MVP_CONTROL_PLANE.md)；其 public edge status 仍需单独验证。
