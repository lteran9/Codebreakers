# Development: disposable, scales to zero, deployed first.
environment = "dev"
location    = "westus3"
owner       = "lteran9"
cost_center = "portfolio"

protect_stateful_resources = false

api_replicas    = { min = 0, max = 2 }
worker_replicas = { min = 0, max = 3 }

postgres_sku_name              = "B_Standard_B1ms"
postgres_storage_mb            = 32768
postgres_backup_retention_days = 7

log_retention_days = 30
log_daily_quota_gb = 0.25

# GitHub Actions jobs in this environment deploy revisions via OIDC.
github_environment = "dev"

budget_amount = 40
