# Production inputs. Not deployed in Phase 8: dev is proven first.
environment = "prod"
location    = "westus3"
owner       = "lteran9"
cost_center = "portfolio"

protect_stateful_resources = true

# A warm API replica avoids cold starts; workers still scale to zero.
api_replicas    = { min = 1, max = 5 }
worker_replicas = { min = 0, max = 5 }

postgres_sku_name              = "B_Standard_B2s"
postgres_storage_mb            = 32768
postgres_backup_retention_days = 14

log_retention_days = 30
log_daily_quota_gb = 2

budget_amount = 100
