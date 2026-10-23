# Serverless Data Pipeline & Security Compliance Engine

> An event-driven AWS pipeline (S3 → SQS → Lambda → DynamoDB, with dead-letter
> handling) plus a Python compliance scanner that detects risky configurations and
> proposes fixes in **dry-run mode by default**.

> **Status: work in progress.** This README separates what is built and tested from
> what is designed but not yet deployed. Nothing below is claimed until it has evidence
> in [`docs/evidence/`](docs/evidence/).

## Project status

| Capability | Status | Evidence |
|---|---|---|
| Lambda processing logic with retries, DLQ & idempotency | ⏳ Planned | — |
| Tests against simulated AWS (moto) | ⏳ Planned | — |
| Compliance scanner (detect + dry-run remediation) | ⏳ Planned | — |
| Terraform (S3, SQS, Lambda, DynamoDB, alarms) — validated, not deployed | ⏳ Planned | — |
| GitHub Actions CI | ⏳ Planned | — |
| Real AWS deployment + AWS Config rules | 🗺️ Future | — |

**Legend:** ✅ implemented & verified · 🧪 simulated locally · ⏳ planned for this phase · 🗺️ future enhancement

## License

[MIT](LICENSE)
