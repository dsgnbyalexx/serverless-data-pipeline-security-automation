# Evidence log

Every result here was produced by running the commands shown, locally on Windows 11 with
Python 3.14. **AWS services are simulated with [moto](https://docs.getmoto.org/)**; nothing
in this folder comes from a real AWS account.

| Type | Meaning |
|---|---|
| **Measured (simulated AWS)** | Real output from running the code against moto's in-memory S3, SQS and DynamoDB |
| **Estimate** | A projection, not measured. None recorded yet. |

---

## 2026-10-24: Data pipeline

### Automated tests (measured, simulated AWS)

| Suite | Tests | Result | Raw output |
|---|---|---|---|
| File parsing and row validation (`test_records.py`) | 24 | All passed | [tests.txt](tests.txt) |
| End-to-end pipeline (`test_pipeline.py`): S3 upload → S3 event → SQS → handler → DynamoDB | 11 | All passed | [tests.txt](tests.txt) |
| Line coverage of `src/` | — | 99% (1 line not exercised: the `continue` that skips non-`ObjectCreated` S3 events) | [tests.txt](tests.txt) |
| Lint and formatting (ruff 0.17.2) | — | No issues | [lint.txt](lint.txt) |

### Failure handling demonstrated by the tests

| Scenario | What the test proves |
|---|---|
| Bad rows in a good file | Good rows are stored. Bad rows are recorded with line numbers and are **not** retried |
| Unusable file (wrong type, missing columns, too large) | Marked `REJECTED` once. Never retried, never sent to the DLQ |
| Transient error (DynamoDB unreachable) | Message received **3 times** (`maxReceiveCount`), then moved to the **dead-letter queue** with the original event intact |
| One bad message in a batch | Only that message is reported in `batchItemFailures`; the rest succeed |
| Same message delivered twice | No duplicate orders, and existing orders are not overwritten |
| URL-encoded object keys | Decoded correctly (moto encodes `/` as `%2F`; real S3 encodes spaces as `+`) |
| S3 `s3:TestEvent` | Ignored |

### Experiment: does the idempotency test actually detect duplicates?

**Method:** temporarily remove `ConditionExpression="attribute_not_exists(pk)"` from
`src/pipeline/store.py`, run `test_duplicate_delivery_is_idempotent`, then restore the file.

**Result:** the test **failed** with `existing orders must not be overwritten`, because the
second delivery replaced every order. With the condition restored, all 35 tests pass. The
test genuinely guards the idempotency guarantee.

---

## 2026-10-24: Compliance scanner

### Automated tests (measured, simulated AWS)

`test_compliance.py`: **14 tests, all passed** ([tests.txt](tests.txt)). The full suite is now
**49 tests, 96% line coverage**, and lint is clean ([lint.txt](lint.txt)).

The tests build a deliberately misconfigured simulated account: 3 S3 buckets, 6 security
groups, and 2 IAM users. They check both directions:

| What is verified | Result |
|---|---|
| Every planted misconfiguration is detected | Exactly 10 expected findings, no more and no fewer |
| Safe resources are **not** flagged (public HTTPS, SSH from a private range, a compliant bucket) | No false positives |
| Access-key age | Flagged at 91 days, not at 30. Inactive keys are never flagged |
| A check that fails (simulated `AccessDenied`) | Reported as an error; the other checks still run |
| Dry run | Plans fixes, and a re-scan shows **no resource changed** |
| `--apply` | Fixes **only** opted-in resources; untagged resources and manual-only rules untouched |
| Narrow fix | Revoking open SSH keeps the private `10.0.0.0/8` source on the same rule |
| Scheduled Lambda | Never applies changes, even with `REMEDIATION_DRY_RUN=false` set |
| CLI | Exit code 1 when findings meet `--fail-on`; JSON output parses |

### Experiment: does the opt-in gate actually protect untagged resources?

**Method:** temporarily replace the opt-in check in `src/compliance/remediation.py` with
"always opted in", run `test_compliance.py`, then restore the file.

**Result:** **5 tests failed**, including the dry-run, opt-in, scheduled-Lambda, and CLI
tests. With the gate restored, all 14 pass. The tests genuinely guard the safety gate.

### Demo run

[compliance-demo.txt](compliance-demo.txt) is the real output of `python scripts/demo_compliance.py`
on a simulated account: detect 4 findings → dry run plans 2 fixes → `--apply` fixes the
2 opted-in issues → the re-scan shows only the 2 manual-review findings.

---

## 2026-10-24: Terraform static validation (nothing deployed)

| Check | Tool | Result | Raw output |
|---|---|---|---|
| Formatting | `terraform fmt -check -recursive` | Pass | [terraform-validate.txt](terraform-validate.txt) |
| Configuration validity | `terraform validate` (Terraform 1.16.6, AWS provider 6.70.0, archive 2.8.1) | `Success! The configuration is valid.` | [terraform-validate.txt](terraform-validate.txt) |
| IaC security | Trivy 0.76.1 | 1 HIGH accepted with a reason, 6 LOW accepted or future; gate passes | [trivy-scan.txt](trivy-scan.txt) |
| Lambda packaging | Built a zip with the same layout as `archive_file` and imported both handlers from it | `pipeline.handler.lambda_handler` and `compliance.handler.lambda_handler` load from the zip | (command output in this log) |
| Provider lock | `.terraform.lock.hcl` | Checksums for linux_amd64 and windows_amd64 | — |

**What this does NOT prove:** that AWS accepts every value at apply time, that IAM
permissions are sufficient at runtime, or anything about cost or latency. Those need a
deployment.

---

## 2026-10-24: CI pipeline

The workflow passes actionlint 1.7.12. Both jobs were replayed locally in Linux containers
from a clean copy (`python:3.14` and `hashicorp/terraform:1.16.6`) and passed before publishing.

**First GitHub Actions run:**
[run 40048184180](https://github.com/dsgnbyalexx/serverless-data-pipeline-security-automation/actions/runs/40048184180).
Both jobs passed: python 40 s, terraform 27 s, as reported by GitHub.

---

### What these tests do NOT prove

- moto does not run Lambda's SQS event source mapping. [`tests/sqs_driver.py`](../../tests/sqs_driver.py)
  re-implements its documented behaviour (batch, delete successes, leave failures), so
  retry timing, concurrency, and scaling on real AWS are untested.
- Real IAM permissions, encryption, and service limits are not enforced by moto.
- No performance or cost measurements exist yet.
