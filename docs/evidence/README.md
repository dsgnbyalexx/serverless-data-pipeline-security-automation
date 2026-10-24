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
| File parsing and row validation (`test_records.py`) | 24 | All passed | [pipeline-tests.txt](pipeline-tests.txt) |
| End-to-end pipeline (`test_pipeline.py`): S3 upload → S3 event → SQS → handler → DynamoDB | 11 | All passed | [pipeline-tests.txt](pipeline-tests.txt) |
| Line coverage of `src/` | — | 99% (1 line not exercised: the `continue` that skips non-`ObjectCreated` S3 events) | [pipeline-tests.txt](pipeline-tests.txt) |
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

### What these tests do NOT prove

- moto does not run Lambda's SQS event source mapping. [`tests/sqs_driver.py`](../../tests/sqs_driver.py)
  re-implements its documented behaviour (batch, delete successes, leave failures), so
  retry timing, concurrency, and scaling on real AWS are untested.
- Real IAM permissions, encryption, and service limits are not enforced by moto.
- No performance or cost measurements exist yet.
