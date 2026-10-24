"""Shared test setup: a simulated AWS account (moto) with the pipeline's resources."""

import json

import boto3
import pytest
from moto import mock_aws

from helpers import BUCKET, MAX_RECEIVE_COUNT, REGION, TABLE
from pipeline import handler


@pytest.fixture(autouse=True)
def fake_aws_credentials(monkeypatch):
    # Fake values so no test can ever reach a real AWS account.
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    monkeypatch.delenv("AWS_PROFILE", raising=False)


@pytest.fixture
def aws(monkeypatch):
    """Create bucket, table, queue + dead-letter queue, and S3 -> SQS notifications."""
    with mock_aws():
        s3 = boto3.client("s3", region_name=REGION)
        sqs = boto3.client("sqs", region_name=REGION)
        dynamodb = boto3.resource("dynamodb", region_name=REGION)

        s3.create_bucket(Bucket=BUCKET)
        table = dynamodb.create_table(
            TableName=TABLE,
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        dlq_url = sqs.create_queue(QueueName="test-ingest-dlq")["QueueUrl"]
        dlq_arn = sqs.get_queue_attributes(QueueUrl=dlq_url, AttributeNames=["QueueArn"])[
            "Attributes"
        ]["QueueArn"]
        queue_url = sqs.create_queue(
            QueueName="test-ingest",
            Attributes={
                # 0 s so a failed message is visible again immediately (real config: minutes)
                "VisibilityTimeout": "0",
                "RedrivePolicy": json.dumps(
                    {"deadLetterTargetArn": dlq_arn, "maxReceiveCount": str(MAX_RECEIVE_COUNT)}
                ),
            },
        )["QueueUrl"]
        queue_arn = sqs.get_queue_attributes(QueueUrl=queue_url, AttributeNames=["QueueArn"])[
            "Attributes"
        ]["QueueArn"]

        s3.put_bucket_notification_configuration(
            Bucket=BUCKET,
            NotificationConfiguration={
                "QueueConfigurations": [{"QueueArn": queue_arn, "Events": ["s3:ObjectCreated:*"]}]
            },
        )

        monkeypatch.setenv("TABLE_NAME", TABLE)
        handler._clients.cache_clear()  # make the handler create clients inside this mock
        yield {"s3": s3, "sqs": sqs, "table": table, "queue_url": queue_url, "dlq_url": dlq_url}
        handler._clients.cache_clear()
