variable "region" {
  type    = string
  default = "us-east-1"
}

variable "environment" {
  description = "Short environment name used in resource names and tags."
  type        = string
  default     = "dev"

  validation {
    condition     = can(regex("^[a-z][a-z0-9]{1,9}$", var.environment))
    error_message = "environment must be 2-10 lowercase letters/digits, starting with a letter."
  }
}

variable "lambda_runtime" {
  description = "Python runtime for both functions; matches the local and CI Python version."
  type        = string
  default     = "python3.14"
}

variable "ingest_timeout_seconds" {
  type    = number
  default = 60
}

variable "max_receive_count" {
  description = "Attempts before a message moves to the dead-letter queue (tests use the same value)."
  type        = number
  default     = 3
}

variable "max_file_bytes" {
  type    = number
  default = 5242880
}

variable "ingest_max_concurrency" {
  description = "Upper limit on concurrent ingest invocations (protects DynamoDB and cost). Minimum 2."
  type        = number
  default     = 2

  validation {
    condition     = var.ingest_max_concurrency >= 2
    error_message = "SQS event source maximum concurrency must be at least 2."
  }
}

variable "compliance_schedule" {
  description = "EventBridge schedule for the compliance scan."
  type        = string
  default     = "rate(1 day)"
}

variable "log_retention_days" {
  type    = number
  default = 14
}

variable "alarm_email" {
  description = "Email for alarm notifications. null = alarms are created without a subscriber."
  type        = string
  default     = null
}

variable "force_destroy_bucket" {
  description = "Dev: true so `terraform destroy` can delete a bucket that still holds files."
  type        = bool
  default     = true
}

variable "table_deletion_protection" {
  description = "Dev: false so the environment can be torn down. Production: true."
  type        = bool
  default     = false
}
