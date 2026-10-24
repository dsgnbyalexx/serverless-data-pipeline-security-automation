# Scheduled compliance scan. Its IAM role is READ-ONLY: even if the code were
# changed to apply fixes, AWS would refuse. (Defense in depth: the code path
# already never applies; the permissions make that impossible as well.)

resource "aws_cloudwatch_log_group" "compliance" {
  name              = "/aws/lambda/${local.name}-compliance"
  retention_in_days = var.log_retention_days
}

resource "aws_iam_role" "compliance" {
  name               = "${local.name}-compliance"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

data "aws_iam_policy_document" "compliance" {
  statement {
    sid       = "WriteOwnLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.compliance.arn}:*"]
  }

  # Exactly the read calls made by src/compliance/checks.py and the opt-in
  # lookups in remediation.py. These APIs do not support narrower resources.
  statement {
    sid = "ReadConfigurationOnly"
    actions = [
      "s3:ListAllMyBuckets",
      "s3:GetBucketPublicAccessBlock",
      "s3:GetBucketPolicy",
      "s3:GetBucketVersioning",
      "s3:GetBucketTagging",
      "ec2:DescribeSecurityGroups",
      "iam:ListUsers",
      "iam:ListAccessKeys",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "compliance" {
  name   = "${local.name}-compliance"
  role   = aws_iam_role.compliance.id
  policy = data.aws_iam_policy_document.compliance.json
}

resource "aws_lambda_function" "compliance" {
  function_name    = "${local.name}-compliance"
  role             = aws_iam_role.compliance.arn
  runtime          = var.lambda_runtime
  handler          = "compliance.handler.lambda_handler"
  filename         = data.archive_file.lambda.output_path
  source_code_hash = data.archive_file.lambda.output_base64sha256
  architectures    = ["arm64"]
  timeout          = 120
  memory_size      = 256

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.compliance.name
  }

  depends_on = [aws_iam_role_policy.compliance, aws_cloudwatch_log_group.compliance]
}

# EventBridge rule: run the scan on a schedule (default once a day).
resource "aws_cloudwatch_event_rule" "compliance" {
  name                = "${local.name}-compliance-scan"
  description         = "Scheduled read-only compliance scan"
  schedule_expression = var.compliance_schedule
}

resource "aws_cloudwatch_event_target" "compliance" {
  rule = aws_cloudwatch_event_rule.compliance.name
  arn  = aws_lambda_function.compliance.arn
}

resource "aws_lambda_permission" "compliance_schedule" {
  statement_id  = "AllowEventBridgeSchedule"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.compliance.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.compliance.arn
}
