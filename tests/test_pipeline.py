"""End-to-end pipeline tests against simulated AWS (moto):
S3 upload -> S3 event notification -> SQS -> handler -> DynamoDB."""

import json
from decimal import Decimal
from urllib.parse import unquote_plus

from helpers import (
    BUCKET,
    MAX_RECEIVE_COUNT,
    csv_file,
    file_summaries,
    queue_depth,
    stored_orders,
    upload,
)
from pipeline import handler
from pipeline.handler import lambda_handler
from sqs_driver import drain

VALID = csv_file(
    "ORD-1,a@example.com,10.00,USD,2026-10-24T09:00:00Z",
    "ORD-2,b@example.com,20.50,EUR,2026-10-24T09:05:00Z",
    "ORD-3,c@example.com,7.25,GBP,2026-10-24T09:10:00Z",
)


def s3_event(key: str, bucket: str = BUCKET, etag: str = "abc123") -> str:
    """An S3 ObjectCreated notification body, in the format AWS sends to SQS."""
    return json.dumps(
        {
            "Records": [
                {
                    "eventName": "ObjectCreated:Put",
                    "s3": {"bucket": {"name": bucket}, "object": {"key": key, "eTag": etag}},
                }
            ]
        }
    )


def sqs_event(*bodies: str) -> dict:
    return {
        "Records": [
            {"messageId": f"msg-{i}", "body": body, "attributes": {"ApproximateReceiveCount": "1"}}
            for i, body in enumerate(bodies)
        ]
    }


def test_valid_file_flows_end_to_end(aws):
    upload(aws, "incoming/orders.csv", VALID)
    drain(aws["sqs"], aws["queue_url"])

    orders = stored_orders(aws)
    assert set(orders) == {"ORD-1", "ORD-2", "ORD-3"}
    assert orders["ORD-2"]["amount"] == Decimal("20.50")
    assert orders["ORD-1"]["source_file"] == f"s3://{BUCKET}/incoming/orders.csv"

    [summary] = file_summaries(aws)
    assert summary["status"] == "PROCESSED" and summary["orders_stored"] == 3
    assert queue_depth(aws) == 0 and queue_depth(aws, "dlq_url") == 0


def test_bad_rows_are_skipped_but_good_rows_are_stored(aws):
    upload(
        aws,
        "incoming/mixed.csv",
        csv_file(
            "ORD-1,a@example.com,10.00,USD,2026-10-24T09:00:00Z",
            "ORD-2,broken-email,10.00,USD,2026-10-24T09:00:00Z",
            "ORD-3,c@example.com,-4,USD,2026-10-24T09:00:00Z",
        ),
    )
    drain(aws["sqs"], aws["queue_url"])

    assert set(stored_orders(aws)) == {"ORD-1"}
    [summary] = file_summaries(aws)
    assert summary["rows_rejected"] == 2
    assert [e["line"] for e in summary["sample_errors"]] == [3, 4]
    assert queue_depth(aws, "dlq_url") == 0  # bad rows are not a reason to retry


def test_duplicate_delivery_is_idempotent(aws):
    upload(aws, "incoming/orders.csv", VALID)
    event = sqs_event(s3_event("incoming/orders.csv"))

    first = lambda_handler(event, None)
    before = stored_orders(aws)
    second = lambda_handler(event, None)  # SQS delivered the same message again
    after = stored_orders(aws)

    assert first == second == {"batchItemFailures": []}
    assert len(after) == 3
    assert {k: v["ingested_at"] for k, v in after.items()} == {
        k: v["ingested_at"] for k, v in before.items()
    }, "existing orders must not be overwritten"
    [summary] = file_summaries(aws)  # same file + version -> same summary item
    assert summary["orders_stored"] == 0 and summary["duplicates_skipped"] == 3


def test_unusable_file_is_rejected_without_retrying(aws):
    upload(aws, "incoming/notes.txt", "hello")
    upload(aws, "incoming/wrong-columns.csv", "id,price\n1,2\n")
    result = drain(aws["sqs"], aws["queue_url"])

    statuses = {s["source_file"].rsplit("/", 1)[1]: s for s in file_summaries(aws)}
    assert statuses["notes.txt"]["status"] == "REJECTED"
    assert "missing required columns" in statuses["wrong-columns.csv"]["reason"]
    assert all(n == 1 for n in result.attempts_per_message.values()), "processed exactly once"
    assert queue_depth(aws) == 0 and queue_depth(aws, "dlq_url") == 0


def test_transient_failure_is_retried_then_moved_to_dead_letter_queue(aws, monkeypatch):
    # Simulate a DynamoDB outage: the table the function writes to is unreachable.
    monkeypatch.setenv("TABLE_NAME", "table-that-does-not-exist")
    handler._clients.cache_clear()

    upload(aws, "incoming/orders.csv", VALID)
    result = drain(aws["sqs"], aws["queue_url"])

    # The queue also holds the s3:TestEvent S3 sends when notifications are set up;
    # it succeeds once. The file event fails every time.
    assert sorted(result.attempts_per_message.values()) == [1, MAX_RECEIVE_COUNT], (
        "test event processed once; file event retried up to maxReceiveCount"
    )
    assert queue_depth(aws) == 0
    assert queue_depth(aws, "dlq_url") == 1, "message parked in the dead-letter queue"

    parked = aws["sqs"].receive_message(QueueUrl=aws["dlq_url"])["Messages"][0]
    parked_key = json.loads(parked["Body"])["Records"][0]["s3"]["object"]["key"]
    assert unquote_plus(parked_key) == "incoming/orders.csv", "DLQ keeps the event for replay"


def test_one_failing_message_does_not_fail_the_whole_batch(aws):
    upload(aws, "incoming/orders.csv", VALID)
    event = sqs_event(
        s3_event("incoming/orders.csv"),
        s3_event("incoming/x.csv", bucket="bucket-that-does-not-exist"),
    )

    response = lambda_handler(event, None)

    assert response == {"batchItemFailures": [{"itemIdentifier": "msg-1"}]}
    assert len(stored_orders(aws)) == 3


def test_url_encoded_keys_are_decoded(aws):
    upload(aws, "daily orders/24 Oct.csv", VALID)
    response = lambda_handler(sqs_event(s3_event("daily+orders/24+Oct.csv")), None)

    assert response == {"batchItemFailures": []}
    assert len(stored_orders(aws)) == 3


def test_s3_test_event_is_ignored(aws):
    body = json.dumps({"Service": "Amazon S3", "Event": "s3:TestEvent", "Bucket": BUCKET})
    assert lambda_handler(sqs_event(body), None) == {"batchItemFailures": []}
    assert file_summaries(aws) == []


def test_deleted_object_is_skipped_not_retried(aws):
    response = lambda_handler(sqs_event(s3_event("incoming/already-deleted.csv")), None)
    assert response == {"batchItemFailures": []}


def test_oversized_file_is_rejected(aws, monkeypatch):
    monkeypatch.setenv("MAX_FILE_BYTES", "50")
    upload(aws, "incoming/big.csv", VALID)
    drain(aws["sqs"], aws["queue_url"])

    [summary] = file_summaries(aws)
    assert summary["status"] == "REJECTED" and "larger than 50 bytes" in summary["reason"]


def test_metrics_are_emitted_in_cloudwatch_embedded_format(aws, capsys):
    upload(aws, "incoming/orders.csv", VALID)
    capsys.readouterr()
    lambda_handler(sqs_event(s3_event("incoming/orders.csv")), None)

    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    [metrics] = [line for line in lines if "_aws" in line]
    names = {m["Name"] for m in metrics["_aws"]["CloudWatchMetrics"][0]["Metrics"]}
    assert {"OrdersStored", "MessagesFailed"} <= names
    assert metrics["OrdersStored"] == 3 and metrics["MessagesFailed"] == 0
