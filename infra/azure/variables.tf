variable "environment" {
  description = "Deployment environment name; also selects the state container."
  type        = string

  validation {
    condition     = contains(["dev", "prod"], var.environment)
    error_message = "environment must be dev or prod."
  }
}

variable "location" {
  description = "Azure region for every resource in the environment."
  type        = string
  default     = "westus3"
}

variable "owner" {
  description = "Owner tag value: the person or team accountable for the environment."
  type        = string
}

variable "cost_center" {
  description = "Cost center tag value used for cost reporting."
  type        = string
}

variable "image_tag" {
  description = "Immutable image tag in the registry, for example a Git commit SHA."
  type        = string

  validation {
    condition     = can(regex("^[0-9a-f]{40}$", var.image_tag))
    error_message = "image_tag must be a full lowercase Git commit SHA."
  }
}

variable "protect_stateful_resources" {
  description = "Add delete locks and purge protection to stateful resources. Disable only for disposable environments."
  type        = bool
}

variable "api_replicas" {
  description = "Minimum and maximum API replicas. A minimum of 0 scales to zero when idle."
  type = object({
    min = number
    max = number
  })
}

variable "worker_replicas" {
  description = "Minimum and maximum worker replicas, scaled by PostgreSQL queue depth."
  type = object({
    min = number
    max = number
  })
}

variable "api_concurrent_requests_per_replica" {
  description = "HTTP scale rule target for concurrent requests per API replica."
  type        = number
  default     = 20
}

variable "worker_jobs_per_replica" {
  description = "Queue scale rule target for queued jobs per worker replica."
  type        = number
  default     = 5
}

variable "analysis_retention_days" {
  description = "Days that finished analyses are kept before the retention job deletes them."
  type        = number
  default     = 7
}

variable "postgres_sku_name" {
  description = "PostgreSQL Flexible Server compute SKU."
  type        = string
  default     = "B_Standard_B1ms"
}

variable "postgres_storage_mb" {
  description = "PostgreSQL Flexible Server storage size in MB."
  type        = number
  default     = 32768
}

variable "postgres_backup_retention_days" {
  description = "Point-in-time restore window for PostgreSQL backups (7-35 days)."
  type        = number
  default     = 7
}

variable "log_retention_days" {
  description = "Log Analytics retention in days."
  type        = number
  default     = 30
}

variable "log_daily_quota_gb" {
  description = "Daily Log Analytics ingestion cap in GB; -1 removes the cap."
  type        = number
  default     = 1
}

variable "budget_amount" {
  description = "Monthly budget in the billing currency for the environment resource group."
  type        = number
}

variable "budget_contact_emails" {
  description = "Email addresses notified by budget alerts. Set in a git-ignored *.auto.tfvars file."
  type        = list(string)

  validation {
    condition     = length(var.budget_contact_emails) > 0
    error_message = "Provide at least one budget alert email address."
  }
}
