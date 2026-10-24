# One table holds orders (pk = "ORDER#<id>") and file summaries (pk = "FILE#...").
# On-demand billing: no capacity to plan, and no charge when idle (storage aside).

resource "aws_dynamodb_table" "orders" {
  name                        = "${local.name}-processed"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "pk"
  deletion_protection_enabled = var.table_deletion_protection

  attribute {
    name = "pk"
    type = "S"
  }

  # Restore the table to any second in the last 35 days.
  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true # uses the AWS-managed KMS key for DynamoDB
  }
}
