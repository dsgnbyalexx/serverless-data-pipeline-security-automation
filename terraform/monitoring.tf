# Alarms answer "is something wrong right now?" and notify an SNS topic.
# Custom metrics (namespace DataPipeline) come from the EMF log lines the
# functions print; see src/pipeline/observability.py.

# Accepted: CloudWatch alarms cannot publish to a topic encrypted with the
# AWS-managed SNS key, and a customer-managed key adds monthly cost. Messages
# contain only alarm metadata, never order data.
#trivy:ignore:AWS-0095
resource "aws_sns_topic" "alarms" {
  name = "${local.name}-alarms"
}

resource "aws_sns_topic_subscription" "alarm_email" {
  count     = var.alarm_email == null ? 0 : 1
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email" # AWS emails a confirmation link first
  endpoint  = var.alarm_email
}

locals {
  alarm_defaults = {
    period              = 300
    evaluation_periods  = 1
    comparison_operator = "GreaterThanThreshold"
    treat_missing_data  = "notBreaching"
  }
}

resource "aws_cloudwatch_metric_alarm" "dlq_not_empty" {
  alarm_name          = "${local.name}-dlq-not-empty"
  alarm_description   = "Messages in the dead-letter queue: files failed ${var.max_receive_count} times. Investigate, fix, then redrive."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = aws_sqs_queue.ingest_dlq.name }
  statistic           = "Maximum"
  threshold           = 0
  period              = local.alarm_defaults.period
  evaluation_periods  = local.alarm_defaults.evaluation_periods
  comparison_operator = local.alarm_defaults.comparison_operator
  treat_missing_data  = local.alarm_defaults.treat_missing_data
  alarm_actions       = [aws_sns_topic.alarms.arn]
}

resource "aws_cloudwatch_metric_alarm" "ingest_backlog" {
  alarm_name          = "${local.name}-ingest-backlog"
  alarm_description   = "Oldest message waiting more than 15 minutes: the pipeline is falling behind."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateAgeOfOldestMessage"
  dimensions          = { QueueName = aws_sqs_queue.ingest.name }
  statistic           = "Maximum"
  threshold           = 900
  period              = local.alarm_defaults.period
  evaluation_periods  = local.alarm_defaults.evaluation_periods
  comparison_operator = local.alarm_defaults.comparison_operator
  treat_missing_data  = local.alarm_defaults.treat_missing_data
  alarm_actions       = [aws_sns_topic.alarms.arn]
}

# With partial batch responses the function itself rarely "errors", so the
# app-level MessagesFailed metric is the real signal of failing messages.
resource "aws_cloudwatch_metric_alarm" "ingest_messages_failed" {
  alarm_name          = "${local.name}-ingest-messages-failed"
  alarm_description   = "The ingest function reported failed messages (they will be retried)."
  namespace           = "DataPipeline"
  metric_name         = "MessagesFailed"
  dimensions          = { Function = "ingest" }
  statistic           = "Sum"
  threshold           = 0
  period              = local.alarm_defaults.period
  evaluation_periods  = local.alarm_defaults.evaluation_periods
  comparison_operator = local.alarm_defaults.comparison_operator
  treat_missing_data  = local.alarm_defaults.treat_missing_data
  alarm_actions       = [aws_sns_topic.alarms.arn]
}

resource "aws_cloudwatch_metric_alarm" "function_errors" {
  for_each = {
    ingest     = aws_lambda_function.ingest.function_name
    compliance = aws_lambda_function.compliance.function_name
  }

  alarm_name          = "${each.value}-errors"
  alarm_description   = "The ${each.key} function crashed or timed out."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = each.value }
  statistic           = "Sum"
  threshold           = 0
  period              = local.alarm_defaults.period
  evaluation_periods  = local.alarm_defaults.evaluation_periods
  comparison_operator = local.alarm_defaults.comparison_operator
  treat_missing_data  = local.alarm_defaults.treat_missing_data
  alarm_actions       = [aws_sns_topic.alarms.arn]
}

resource "aws_cloudwatch_metric_alarm" "compliance_high_findings" {
  alarm_name          = "${local.name}-compliance-high-findings"
  alarm_description   = "The compliance scan found CRITICAL or HIGH findings. See the compliance function logs."
  comparison_operator = local.alarm_defaults.comparison_operator
  threshold           = 0
  evaluation_periods  = 1
  treat_missing_data  = local.alarm_defaults.treat_missing_data
  alarm_actions       = [aws_sns_topic.alarms.arn]

  metric_query {
    id          = "serious"
    expression  = "critical + high"
    label       = "CRITICAL + HIGH findings"
    return_data = true
  }

  metric_query {
    id = "critical"
    metric {
      namespace   = "DataPipeline"
      metric_name = "FindingsCritical"
      dimensions  = { Function = "compliance-scan" }
      stat        = "Maximum"
      period      = 86400
    }
  }

  metric_query {
    id = "high"
    metric {
      namespace   = "DataPipeline"
      metric_name = "FindingsHigh"
      dimensions  = { Function = "compliance-scan" }
      stat        = "Maximum"
      period      = 86400
    }
  }
}
