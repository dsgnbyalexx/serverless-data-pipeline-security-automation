"""DynamoDB writes. Every write is idempotent: repeating it has no further effect.

SQS and S3 event notifications deliver *at least once*, so the same file can be
processed twice. A conditional write ("only if this order id is new") makes a
repeat harmless: the second attempt is counted as a duplicate, not stored again.
"""

from datetime import UTC, datetime

from botocore.exceptions import ClientError

from pipeline.records import Order, RowError

MAX_STORED_ERRORS = 20  # keep file summaries small; full detail is in the logs


def put_order_if_new(table, order: Order, source: str) -> bool:
    """Store an order. Returns False if an order with this id already exists."""
    try:
        table.put_item(
            Item={
                "pk": f"ORDER#{order.order_id}",
                "order_id": order.order_id,
                "customer_email": order.customer_email,
                "amount": order.amount,
                "currency": order.currency,
                "created_at": order.created_at,
                "source_file": source,
                "ingested_at": datetime.now(UTC).isoformat(),
            },
            ConditionExpression="attribute_not_exists(pk)",
        )
        return True
    except ClientError as err:
        if err.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise  # anything else (throttling, outage) is transient: let SQS retry


def put_file_summary(
    table,
    source: str,
    version: str,
    status: str,
    *,
    stored: int = 0,
    duplicates: int = 0,
    row_errors: list[RowError] | None = None,
    reason: str | None = None,
) -> None:
    """Record what happened to a file. Keyed by file + version, so a redelivered
    event overwrites the same summary instead of creating a second one."""
    row_errors = row_errors or []
    item = {
        "pk": f"FILE#{source}#{version}",
        "source_file": source,
        "status": status,
        "orders_stored": stored,
        "duplicates_skipped": duplicates,
        "rows_rejected": len(row_errors),
        "sample_errors": [
            {"line": e.line, "errors": list(e.errors)} for e in row_errors[:MAX_STORED_ERRORS]
        ],
        "processed_at": datetime.now(UTC).isoformat(),
    }
    if reason:
        item["reason"] = reason
    table.put_item(Item=item)
