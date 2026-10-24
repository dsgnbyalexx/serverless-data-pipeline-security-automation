"""Stand-in for Lambda's SQS event source mapping, which moto does not run.

It mirrors the documented behaviour with ReportBatchItemFailures enabled:
receive up to 10 messages, invoke the function with them, delete the messages
that succeeded, and leave failed ones on the queue to become visible again.
SQS itself (simulated by moto) moves a message to the dead-letter queue once it
has been received more than maxReceiveCount times.
"""

from dataclasses import dataclass, field

from pipeline.handler import lambda_handler


@dataclass
class DrainResult:
    invocations: int = 0
    attempts_per_message: dict[str, int] = field(default_factory=dict)


def drain(sqs, queue_url: str, max_rounds: int = 20) -> DrainResult:
    result = DrainResult()
    for _ in range(max_rounds):
        response = sqs.receive_message(
            QueueUrl=queue_url,
            MaxNumberOfMessages=10,
            AttributeNames=["All"],
            WaitTimeSeconds=0,
        )
        messages = response.get("Messages", [])
        if not messages:
            break

        event = {
            "Records": [
                {
                    "messageId": m["MessageId"],
                    "receiptHandle": m["ReceiptHandle"],
                    "body": m["Body"],
                    "attributes": m.get("Attributes", {}),
                    "eventSource": "aws:sqs",
                }
                for m in messages
            ]
        }
        response = lambda_handler(event, context=None)
        result.invocations += 1

        failed = {f["itemIdentifier"] for f in response["batchItemFailures"]}
        for m in messages:
            result.attempts_per_message[m["MessageId"]] = (
                result.attempts_per_message.get(m["MessageId"], 0) + 1
            )
            if m["MessageId"] not in failed:
                sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=m["ReceiptHandle"])
    return result
