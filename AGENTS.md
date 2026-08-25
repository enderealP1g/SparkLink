# SparkLink Agent Collaboration Policy

## Project Nature

- SparkLink is both a real operating service and a software-engineering learning project.
- The learning path is `Product Requirements → Architecture → Implementation → Testing → Deployment → Operations → Troubleshooting`.
- Do not sacrifice real-service reliability for teaching purposes; do not over-design for the sake of enterprise completeness.

## Source of Truth

- Reviewed and committed `requirements`, `architecture`, `decisions`, and related project documents are engineering facts for this repository.
- The Product Owner has final authority over business rules and unresolved product questions.
- Never treat an `Open Question` as a decided fact without an explicit decision.
- If code or runtime state conflicts with documented facts, report the conflict and evidence; do not silently choose a side.

## Documentation Language Policy

- Maintain one canonical document per topic; do not create separate English and Chinese versions.
- Use Chinese by default for explanatory text, design rationale, requirements, and troubleshooting guidance.
- Keep `Domain Terms`, code/API identifiers, protocol and component names, filenames, and `Requirement`/`Decision` IDs in English.
- Aim for Chinese to carry understanding while English preserves engineering-term consistency.
- Example: `进入新的 Customer Billing Cycle 时，不得删除历史 Usage。`

## Scope Discipline

- Follow the current Iteration and task scope strictly.
- Record future needs as `Open Question` or `Deferred Work`; do not implement them automatically.
- During a Requirements phase, do not enter implementation without explicit scope.
- Avoid speculative abstractions and unnecessary enterprise complexity.

## Production and Secret Safety

- SparkLink has real users and running VPS infrastructure.
- Without explicit authorization for the current task, do not modify VPSs, production configuration, or the existing data plane.
- Never write credentials, UUIDs, subscription tokens, private keys, passwords, or other secret/private runtime material to Git.
- Metering or management-plane work must not unnecessarily disrupt the existing proxy data plane.
