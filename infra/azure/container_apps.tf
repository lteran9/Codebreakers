resource "azurerm_container_app_environment" "main" {
  name                       = "cae-${local.name}"
  location                   = azurerm_resource_group.main.location
  resource_group_name        = azurerm_resource_group.main.name
  logs_destination           = "log-analytics"
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id
  tags                       = local.tags
}

locals {
  image              = "${azurerm_container_registry.main.login_server}/codebreakers:${var.image_tag}"
  database_secret    = "database-url"
  api_port           = 8000
analysis_queue_sql = <<-SQL
  SELECT COUNT(*)
  FROM analysis_job_queue
  WHERE dead_lettered_at IS NULL
    AND available_at <= NOW()
    AND (locked_until IS NULL OR locked_until <= NOW())
SQL

  # Shared by every app and job; the database URL is added as a secret reference.
  plain_env = {
    CODEBREAKERS_ANALYSIS_QUEUE           = "postgres"
    CODEBREAKERS_ANALYSIS_RETENTION_DAYS  = tostring(var.analysis_retention_days)
    APPLICATIONINSIGHTS_CONNECTION_STRING = azurerm_application_insights.main.connection_string
    AZURE_CLIENT_ID                       = azurerm_user_assigned_identity.app.client_id
  }

  # One image, three long-running roles (ADR-0010). Relay and worker have no
  # HTTP server, and Container Apps probes cannot run commands, so only the
  # API has health probes. Both scale on PostgreSQL row counts via KEDA.
  apps = {
    api = {
      args         = ["serve", "--host", "0.0.0.0", "--port", tostring(local.api_port), "--graceful-timeout", "10"]
      cpu          = 0.5
      memory       = "1Gi"
      min_replicas = var.api_replicas.min
      max_replicas = var.api_replicas.max
      grace_period = 15
      ingress      = true
      scale_query  = null
      scale_target = null
    }
    relay = {
      args         = ["relay"]
      cpu          = 0.25
      memory       = "0.5Gi"
      min_replicas = 0
      max_replicas = 1
      grace_period = 15
      ingress      = false
      scale_query  = "SELECT COUNT(*) FROM analysis_outbox"
      scale_target = 1
    }
    worker = {
      args         = ["worker"]
      cpu          = 0.5
      memory       = "1Gi"
      min_replicas = var.worker_replicas.min
      max_replicas = var.worker_replicas.max
      # Must exceed the 30 s analysis time budget so an in-flight job finishes.
      grace_period = 45
      ingress      = false
      scale_query  = local.analysis_queue_sql
      scale_target = var.worker_jobs_per_replica
    }
  }

  jobs = {
    migrate = {
      command  = ["alembic"]
      args     = ["upgrade", "head"]
      schedule = null
    }
    retention = {
      command  = ["python"]
      args     = ["-m", "codebreakers.infrastructure.persistence.retention"]
      schedule = "17 3 * * *"
    }
  }
}

resource "azurerm_container_app" "app" {
  for_each = local.apps

  name                         = "ca-${local.name}-${each.key}"
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = azurerm_resource_group.main.name
  revision_mode                = "Single"
  tags                         = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.app.id]
  }

  registry {
    server   = azurerm_container_registry.main.login_server
    identity = azurerm_user_assigned_identity.app.id
  }

  secret {
    name                = local.database_secret
    key_vault_secret_id = azurerm_key_vault_secret.database_url.versionless_id
    identity            = azurerm_user_assigned_identity.app.id
  }

  dynamic "ingress" {
    for_each = each.value.ingress ? [local.api_port] : []

    content {
      external_enabled           = true
      target_port                = ingress.value
      transport                  = "auto"
      allow_insecure_connections = false

      traffic_weight {
        latest_revision = true
        percentage      = 100
      }
    }
  }

  template {
    min_replicas                     = each.value.min_replicas
    max_replicas                     = each.value.max_replicas
    termination_grace_period_seconds = each.value.grace_period

    container {
      name   = each.key
      image  = local.image
      cpu    = each.value.cpu
      memory = each.value.memory
      args   = each.value.args

      dynamic "env" {
        for_each = local.plain_env

        content {
          name  = env.key
          value = env.value
        }
      }

      env {
        name        = "CODEBREAKERS_DATABASE_URL"
        secret_name = local.database_secret
      }

      dynamic "startup_probe" {
        for_each = each.value.ingress ? [local.api_port] : []

        content {
          transport               = "HTTP"
          port                    = startup_probe.value
          path                    = "/health/live"
          interval_seconds        = 2
          failure_count_threshold = 30
        }
      }

      dynamic "liveness_probe" {
        for_each = each.value.ingress ? [local.api_port] : []

        content {
          transport = "HTTP"
          port      = liveness_probe.value
          path      = "/health/live"
        }
      }

      dynamic "readiness_probe" {
        for_each = each.value.ingress ? [local.api_port] : []

        content {
          transport = "HTTP"
          port      = readiness_probe.value
          path      = "/health/ready"
        }
      }
    }

    dynamic "http_scale_rule" {
      for_each = each.value.ingress ? [var.api_concurrent_requests_per_replica] : []

      content {
        name                = "http-concurrency"
        concurrent_requests = tostring(http_scale_rule.value)
      }
    }

    dynamic "custom_scale_rule" {
      for_each = each.value.scale_query == null ? [] : [each.value]

      content {
        name             = "postgres-rows"
        custom_rule_type = "postgresql"
        metadata = {
          query                      = custom_scale_rule.value.scale_query
          targetQueryValue           = tostring(custom_scale_rule.value.scale_target)
          activationTargetQueryValue = "0"
        }

        authentication {
          secret_name       = local.database_secret
          trigger_parameter = "connection"
        }
      }
    }
  }

  depends_on = [time_sleep.app_rbac_propagation]
}

resource "azurerm_container_app_job" "job" {
  for_each = local.jobs

  name                         = "caj-${local.name}-${each.key}"
  location                     = azurerm_resource_group.main.location
  resource_group_name          = azurerm_resource_group.main.name
  container_app_environment_id = azurerm_container_app_environment.main.id
  replica_timeout_in_seconds   = 600
  replica_retry_limit          = 1
  tags                         = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.app.id]
  }

  registry {
    server   = azurerm_container_registry.main.login_server
    identity = azurerm_user_assigned_identity.app.id
  }

  secret {
    name                = local.database_secret
    key_vault_secret_id = azurerm_key_vault_secret.database_url.versionless_id
    identity            = azurerm_user_assigned_identity.app.id
  }

  dynamic "manual_trigger_config" {
    for_each = each.value.schedule == null ? [1] : []

    content {
      parallelism              = 1
      replica_completion_count = 1
    }
  }

  dynamic "schedule_trigger_config" {
    for_each = each.value.schedule == null ? [] : [each.value.schedule]

    content {
      cron_expression          = schedule_trigger_config.value
      parallelism              = 1
      replica_completion_count = 1
    }
  }

  template {
    container {
      name    = each.key
      image   = local.image
      cpu     = 0.25
      memory  = "0.5Gi"
      command = each.value.command
      args    = each.value.args

      dynamic "env" {
        for_each = local.plain_env

        content {
          name  = env.key
          value = env.value
        }
      }

      env {
        name        = "CODEBREAKERS_DATABASE_URL"
        secret_name = local.database_secret
      }
    }
  }

  depends_on = [time_sleep.app_rbac_propagation]
}
