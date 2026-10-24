output "incoming_bucket" {
  description = "Upload CSV files under incoming/ in this bucket."
  value       = aws_s3_bucket.incoming.bucket
}

output "ingest_queue_url" {
  value = aws_sqs_queue.ingest.url
}

output "dead_letter_queue_url" {
  value = aws_sqs_queue.ingest_dlq.url
}

output "orders_table" {
  value = aws_dynamodb_table.orders.name
}

output "ingest_function" {
  value = aws_lambda_function.ingest.function_name
}

output "compliance_function" {
  value = aws_lambda_function.compliance.function_name
}

output "alarm_topic_arn" {
  value = aws_sns_topic.alarms.arn
}
