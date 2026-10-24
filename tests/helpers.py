"""Constants and small helpers shared by the tests."""

REGION = "us-east-1"
BUCKET = "test-incoming-orders"
TABLE = "test-processed-orders"
MAX_RECEIVE_COUNT = 3

HEADER = "order_id,customer_email,amount,currency,created_at\n"


def csv_file(*rows: str) -> str:
    return HEADER + "".join(row + "\n" for row in rows)


def upload(aws, key: str, content: str | bytes) -> None:
    body = content.encode() if isinstance(content, str) else content
    aws["s3"].put_object(Bucket=BUCKET, Key=key, Body=body)


def queue_depth(aws, which: str = "queue_url") -> int:
    attrs = aws["sqs"].get_queue_attributes(
        QueueUrl=aws[which],
        AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"],
    )["Attributes"]
    return int(attrs["ApproximateNumberOfMessages"]) + int(
        attrs["ApproximateNumberOfMessagesNotVisible"]
    )


def stored_orders(aws) -> dict[str, dict]:
    items = aws["table"].scan()["Items"]
    return {i["order_id"]: i for i in items if i["pk"].startswith("ORDER#")}


def file_summaries(aws) -> list[dict]:
    return [i for i in aws["table"].scan()["Items"] if i["pk"].startswith("FILE#")]
