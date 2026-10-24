# The queue decouples S3 from Lambda: it absorbs bursts, and messages wait
# safely if the function is failing. After max_receive_count failed attempts a
# message moves to the dead-letter queue (DLQ) for investigation and replay.

resource "aws_sqs_queue" "ingest_dlq" {
  name                      = "${local.name}-ingest-dlq"
  message_retention_seconds = 1209600 # 14 days (the maximum) to investigate
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "ingest" {
  name                      = "${local.name}-ingest"
  message_retention_seconds = 345600 # 4 days
  sqs_managed_sse_enabled   = true

  # AWS recommends at least 6x the function timeout, so a message is not
  # handed to a second invocation while the first is still working on it.
  visibility_timeout_seconds = var.ingest_timeout_seconds * 6

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.ingest_dlq.arn
    maxReceiveCount     = var.max_receive_count
  })
}

# Only the main queue may send messages to this DLQ.
resource "aws_sqs_queue_redrive_allow_policy" "ingest_dlq" {
  queue_url = aws_sqs_queue.ingest_dlq.id
  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.ingest.arn]
  })
}

# Allow S3 to send messages, but only from OUR bucket in OUR account.
data "aws_iam_policy_document" "ingest_queue" {
  statement {
    sid       = "AllowIncomingBucketNotifications"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.ingest.arn]
    principals {
      type        = "Service"
      identifiers = ["s3.amazonaws.com"]
    }
    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_s3_bucket.incoming.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_sqs_queue_policy" "ingest" {
  queue_url = aws_sqs_queue.ingest.id
  policy    = data.aws_iam_policy_document.ingest_queue.json
}
