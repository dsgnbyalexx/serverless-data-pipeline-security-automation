# Ingest function: triggered by the SQS queue, writes to DynamoDB.

resource "aws_cloudwatch_log_group" "ingest" {
  name              = "/aws/lambda/${local.name}-ingest"
  retention_in_days = var.log_retention_days
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ingest" {
  name               = "${local.name}-ingest"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

data "aws_iam_policy_document" "ingest" {
  statement {
    sid       = "WriteOwnLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.ingest.arn}:*"]
  }

  statement {
    sid       = "ConsumeIngestQueue"
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes", "sqs:ChangeMessageVisibility"]
    resources = [aws_sqs_queue.ingest.arn]
  }

  statement {
    sid       = "ReadIncomingFiles"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.incoming.arn}/incoming/*"]
  }

  # Without ListBucket, S3 answers AccessDenied (not NoSuchKey) for a missing
  # object, and the handler would retry a deleted file all the way to the DLQ.
  statement {
    sid       = "DistinguishMissingFiles"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.incoming.arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["incoming/*"]
    }
  }

  statement {
    sid       = "WriteOrders"
    actions   = ["dynamodb:PutItem"]
    resources = [aws_dynamodb_table.orders.arn]
  }
}

resource "aws_iam_role_policy" "ingest" {
  name   = "${local.name}-ingest"
  role   = aws_iam_role.ingest.id
  policy = data.aws_iam_policy_document.ingest.json
}

resource "aws_lambda_function" "ingest" {
  function_name    = "${local.name}-ingest"
  role             = aws_iam_role.ingest.arn
  runtime          = var.lambda_runtime
  handler          = "pipeline.handler.lambda_handler"
  filename         = data.archive_file.lambda.output_path
  source_code_hash = data.archive_file.lambda.output_base64sha256
  architectures    = ["arm64"] # Graviton: lower price per GB-second; pure Python runs as-is
  timeout          = var.ingest_timeout_seconds
  memory_size      = 256

  environment {
    variables = {
      TABLE_NAME     = aws_dynamodb_table.orders.name
      MAX_FILE_BYTES = tostring(var.max_file_bytes)
    }
  }

  logging_config {
    log_format = "Text" # the code already writes one JSON object per line
    log_group  = aws_cloudwatch_log_group.ingest.name
  }

  depends_on = [aws_iam_role_policy.ingest, aws_cloudwatch_log_group.ingest]
}

# The "event source mapping" is the AWS-managed poller that reads the queue and
# invokes the function (tests/sqs_driver.py imitates it locally).
resource "aws_lambda_event_source_mapping" "ingest" {
  event_source_arn                   = aws_sqs_queue.ingest.arn
  function_name                      = aws_lambda_function.ingest.arn
  batch_size                         = 10
  maximum_batching_window_in_seconds = 5
  function_response_types            = ["ReportBatchItemFailures"] # partial batch failures

  scaling_config {
    maximum_concurrency = var.ingest_max_concurrency
  }
}
