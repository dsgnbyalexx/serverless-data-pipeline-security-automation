"""Lambda entry point, triggered by SQS messages that carry S3 event notifications.

Error handling has three levels:
  * Bad ROW      -> skipped and recorded in the file summary; good rows still stored.
  * Bad FILE     -> (wrong format, too big, missing columns) recorded as REJECTED.
                    Not retried: retrying a broken file can never succeed.
  * Transient    -> (DynamoDB throttled, S3 unavailable) the exception propagates,
                    the message is reported as failed, and SQS retries it. After
                    maxReceiveCount attempts SQS moves it to the dead-letter queue.

Partial batch responses ("batchItemFailures") mean one failing message in a batch
of ten does not force the other nine to be processed again.
"""

import json
import os
from functools import cache
from urllib.parse import unquote_plus

import boto3
from botocore.exceptions import ClientError

from pipeline.observability import emit_metrics, log
from pipeline.records import FileRejected, parse_orders_csv
from pipeline.store import put_file_summary, put_order_if_new

DEFAULT_MAX_FILE_BYTES = 5 * 1024 * 1024


@cache
def _clients():
    # Created once per Lambda container and reused across invocations (faster).
    return boto3.client("s3"), boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])


def _max_file_bytes() -> int:
    return int(os.environ.get("MAX_FILE_BYTES", DEFAULT_MAX_FILE_BYTES))


def process_s3_object(bucket: str, key: str, version: str) -> dict[str, int]:
    s3, table = _clients()
    source = f"s3://{bucket}/{key}"
    counts = {"OrdersStored": 0, "DuplicatesSkipped": 0, "RowsRejected": 0, "FilesRejected": 0}

    try:
        if not key.lower().endswith(".csv"):
            raise FileRejected("only .csv files are accepted")
        try:
            obj = s3.get_object(Bucket=bucket, Key=key)
        except ClientError as err:
            if err.response["Error"]["Code"] == "NoSuchKey":
                log("WARNING", "object no longer exists; skipping", source=source)
                return counts
            raise
        if obj["ContentLength"] > _max_file_bytes():
            raise FileRejected(f"file is larger than {_max_file_bytes()} bytes")
        orders, row_errors = parse_orders_csv(obj["Body"].read())
    except FileRejected as err:
        put_file_summary(table, source, version, "REJECTED", reason=str(err))
        log("WARNING", "file rejected", source=source, reason=str(err))
        counts["FilesRejected"] = 1
        return counts

    for order in orders:
        if put_order_if_new(table, order, source):
            counts["OrdersStored"] += 1
        else:
            counts["DuplicatesSkipped"] += 1
    counts["RowsRejected"] = len(row_errors)

    put_file_summary(
        table,
        source,
        version,
        "PROCESSED",
        stored=counts["OrdersStored"],
        duplicates=counts["DuplicatesSkipped"],
        row_errors=row_errors,
    )
    log("INFO", "file processed", source=source, **counts)
    return counts


def process_message(body: str) -> dict[str, int]:
    notification = json.loads(body)
    totals = {"OrdersStored": 0, "DuplicatesSkipped": 0, "RowsRejected": 0, "FilesRejected": 0}

    # S3 sends this once when notifications are first configured. Nothing to do.
    if notification.get("Event") == "s3:TestEvent":
        log("INFO", "ignoring S3 test event")
        return totals

    for record in notification.get("Records", []):
        if not record.get("eventName", "").startswith("ObjectCreated:"):
            continue
        bucket = record["s3"]["bucket"]["name"]
        key = unquote_plus(record["s3"]["object"]["key"])  # S3 URL-encodes keys ("a b" -> "a+b")
        version = record["s3"]["object"].get("versionId") or record["s3"]["object"].get("eTag", "")
        for name, value in process_s3_object(bucket, key, version).items():
            totals[name] += value
    return totals


def lambda_handler(event, context):
    failures = []
    totals = {"OrdersStored": 0, "DuplicatesSkipped": 0, "RowsRejected": 0, "FilesRejected": 0}

    for message in event.get("Records", []):
        message_id = message["messageId"]
        try:
            for name, value in process_message(message["body"]).items():
                totals[name] += value
        except Exception as err:  # any unexpected error means "retry this message"
            log(
                "ERROR",
                "message failed; SQS will retry it",
                message_id=message_id,
                receive_count=message.get("attributes", {}).get("ApproximateReceiveCount"),
                error=f"{type(err).__name__}: {err}",
            )
            failures.append({"itemIdentifier": message_id})

    emit_metrics({**totals, "MessagesFailed": len(failures)}, Function="ingest")
    return {"batchItemFailures": failures}
