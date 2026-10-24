# Incoming files land here. The bucket follows the same rules the compliance
# scanner enforces: public access blocked, HTTPS only, versioning on.

resource "aws_s3_bucket" "incoming" {
  bucket_prefix = "${local.name}-incoming-"
  force_destroy = var.force_destroy_bucket
}

resource "aws_s3_bucket_public_access_block" "incoming" {
  bucket                  = aws_s3_bucket.incoming.id
  block_public_acls       = true
  ignore_public_acls      = true
  block_public_policy     = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "incoming" {
  bucket = aws_s3_bucket.incoming.id
  rule {
    object_ownership = "BucketOwnerEnforced" # disables ACLs entirely
  }
}

# Accepted: objects are encrypted at rest with S3-managed keys (SSE-S3). A
# customer-managed KMS key adds monthly and per-request cost plus key-policy
# management, which is justified for regulated data, not for demo order files.
#trivy:ignore:AWS-0132
resource "aws_s3_bucket_server_side_encryption_configuration" "incoming" {
  bucket = aws_s3_bucket.incoming.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "incoming" {
  bucket = aws_s3_bucket.incoming.id
  versioning_configuration {
    status = "Enabled"
  }
}

# Keep storage costs bounded: processed files expire, and so do old versions.
resource "aws_s3_bucket_lifecycle_configuration" "incoming" {
  bucket = aws_s3_bucket.incoming.id

  rule {
    id     = "expire-incoming-files"
    status = "Enabled"
    filter {
      prefix = "incoming/"
    }
    expiration {
      days = 30
    }
    noncurrent_version_expiration {
      noncurrent_days = 7
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }

  depends_on = [aws_s3_bucket_versioning.incoming]
}

data "aws_iam_policy_document" "incoming_bucket" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.incoming.arn, "${aws_s3_bucket.incoming.arn}/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "incoming" {
  bucket = aws_s3_bucket.incoming.id
  policy = data.aws_iam_policy_document.incoming_bucket.json

  depends_on = [aws_s3_bucket_public_access_block.incoming]
}

# Every new object under incoming/ sends an event to the SQS queue.
resource "aws_s3_bucket_notification" "incoming" {
  bucket = aws_s3_bucket.incoming.id

  queue {
    queue_arn     = aws_sqs_queue.ingest.arn
    events        = ["s3:ObjectCreated:*"]
    filter_prefix = "incoming/"
  }

  # S3 checks that it may write to the queue when the notification is created.
  depends_on = [aws_sqs_queue_policy.ingest]
}
