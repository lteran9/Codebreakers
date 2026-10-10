# Operational alerts and the service dashboard (ADR-0014). Every alert names
# its runbook in docs/operations/alerts.md.

resource "azurerm_monitor_action_group" "operations" {
  name                = "ag-${local.name}"
  resource_group_name = azurerm_resource_group.main.name
  short_name          = substr("cb-${var.environment}", 0, 12)
  tags                = local.tags

  dynamic "email_receiver" {
    for_each = toset(var.budget_contact_emails)

    content {
      name                    = "email-${index(var.budget_contact_emails, email_receiver.value)}"
      email_address           = email_receiver.value
      use_common_alert_schema = true
    }
  }
}

locals {
  runbook = "https://github.com/${var.github_repository}/blob/master/docs/operations/alerts.md"

  postgres_alerts = {
    cpu = {
      metric      = "cpu_percent"
      aggregation = "Average"
      threshold   = 80
      severity    = 2
      description = "PostgreSQL CPU above 80%."
    }
    memory = {
      metric      = "memory_percent"
      aggregation = "Average"
      threshold   = 90
      severity    = 2
      description = "PostgreSQL memory above 90%."
    }
    storage = {
      metric      = "storage_percent"
      aggregation = "Maximum"
      threshold   = 80
      severity    = 1
      description = "PostgreSQL storage above 80%; the server becomes read-only when full."
    }
    connections = {
      metric      = "active_connections"
      aggregation = "Maximum"
      threshold   = var.postgres_connection_alert_threshold
      severity    = 2
      description = "PostgreSQL active connections near the SKU limit."
    }
  }

  # Custom metrics exported by the worker over OpenTelemetry. They only exist
  # while a worker replica runs; KEDA starts one whenever work is queued.
  telemetry_alerts = {
    dead-letters = {
      description = "Analysis jobs were dead-lettered."
      severity    = 2
      column      = "DeadLettered"
      threshold   = 0
      query       = <<-KQL
        customMetrics
        | where name == "codebreakers.jobs.dead_lettered"
        | summarize DeadLettered = sum(valueSum)
      KQL
    }
    queue-age = {
      description = "The oldest ready analysis job has waited more than 5 minutes."
      severity    = 2
      column      = "OldestAgeSeconds"
      threshold   = 300
      query       = <<-KQL
        customMetrics
        | where name == "codebreakers.queue.oldest_age"
        | summarize OldestAgeSeconds = max(valueMax)
      KQL
    }
  }
}

resource "azurerm_monitor_metric_alert" "api_server_errors" {
  name                = "alert-${local.name}-api-5xx"
  resource_group_name = azurerm_resource_group.main.name
  scopes              = [azurerm_container_app.app["api"].id]
  description         = "API returned more than 5 server errors in 15 minutes. Runbook: ${local.runbook}#api-server-errors"
  severity            = 2
  frequency           = "PT5M"
  window_size         = "PT15M"
  tags                = local.tags

  criteria {
    metric_namespace = "Microsoft.App/containerApps"
    metric_name      = "Requests"
    aggregation      = "Total"
    operator         = "GreaterThan"
    threshold        = 5

    dimension {
      name     = "statusCodeCategory"
      operator = "Include"
      values   = ["5xx"]
    }
  }

  action {
    action_group_id = azurerm_monitor_action_group.operations.id
  }
}

# RestartCount is cumulative per replica, so a threshold above zero tolerates
# an isolated restart but fires for a crash-looping API, relay, or worker.
resource "azurerm_monitor_metric_alert" "restarts" {
  for_each = azurerm_container_app.app

  name                = "alert-${local.name}-${each.key}-restarts"
  resource_group_name = azurerm_resource_group.main.name
  scopes              = [each.value.id]
  description         = "A ${each.key} replica restarted more than twice. Runbook: ${local.runbook}#replica-restarts"
  severity            = 2
  frequency           = "PT5M"
  window_size         = "PT15M"
  tags                = local.tags

  criteria {
    metric_namespace = "Microsoft.App/containerApps"
    metric_name      = "RestartCount"
    aggregation      = "Maximum"
    operator         = "GreaterThan"
    threshold        = 2
  }

  action {
    action_group_id = azurerm_monitor_action_group.operations.id
  }
}

resource "azurerm_monitor_metric_alert" "postgres" {
  for_each = local.postgres_alerts

  name                = "alert-${local.name}-postgres-${each.key}"
  resource_group_name = azurerm_resource_group.main.name
  scopes              = [azurerm_postgresql_flexible_server.main.id]
  description         = "${each.value.description} Runbook: ${local.runbook}#database-saturation"
  severity            = each.value.severity
  frequency           = "PT5M"
  window_size         = "PT15M"
  tags                = local.tags

  criteria {
    metric_namespace = "Microsoft.DBforPostgreSQL/flexibleServers"
    metric_name      = each.value.metric
    aggregation      = each.value.aggregation
    operator         = "GreaterThan"
    threshold        = each.value.threshold
  }

  action {
    action_group_id = azurerm_monitor_action_group.operations.id
  }
}

resource "azurerm_monitor_scheduled_query_rules_alert_v2" "telemetry" {
  for_each = local.telemetry_alerts

  name                 = "alert-${local.name}-${each.key}"
  location             = azurerm_resource_group.main.location
  resource_group_name  = azurerm_resource_group.main.name
  scopes               = [azurerm_application_insights.main.id]
  description          = "${each.value.description} Runbook: ${local.runbook}#${each.key}"
  severity             = each.value.severity
  evaluation_frequency = "PT15M"
  window_duration      = "PT15M"
  tags                 = local.tags

  criteria {
    query                   = each.value.query
    time_aggregation_method = "Maximum"
    metric_measure_column   = each.value.column
    operator                = "GreaterThan"
    threshold               = each.value.threshold

    failing_periods {
      minimum_failing_periods_to_trigger_alert = 1
      number_of_evaluation_periods             = 1
    }
  }

  action {
    action_groups = [azurerm_monitor_action_group.operations.id]
  }
}

resource "random_uuid" "workbook" {}

resource "azurerm_application_insights_workbook" "service" {
  name                = random_uuid.workbook.result
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  display_name        = "Codebreakers service (${var.environment})"
  source_id           = lower(azurerm_application_insights.main.id)
  category            = "workbook"
  tags                = local.tags

  data_json = jsonencode({
    version = "Notebook/1.0"
    items = concat(
      [{
        type = 1
        name = "overview"
        content = {
          json = "## Codebreakers ${var.environment}\nAPI rate, errors, and latency (RED), then analysis job throughput, failures, and queue health. Alert runbooks: ${local.runbook}"
        }
      }],
      [for index, panel in local.workbook_panels : {
        type = 3
        name = "panel-${index}"
        content = {
          version       = "KqlItem/1.0"
          title         = panel.title
          query         = panel.query
          size          = 0
          queryType     = 0
          resourceType  = "microsoft.insights/components"
          visualization = "timechart"
          timeContext   = { durationMs = 86400000 }
        }
      }]
    )
    fallbackResourceIds = [lower(azurerm_application_insights.main.id)]
    "$schema"           = "https://github.com/Microsoft/Application-Insights-Workbooks/blob/master/schema/workbook.json"
  })
}

locals {
  workbook_panels = [
    {
      title = "API requests by status"
      query = "requests | summarize count() by bin(timestamp, 5m), tostring(resultCode)"
    },
    {
      title = "API latency percentiles (ms)"
      query = "requests | summarize percentiles(duration, 50, 95, 99) by bin(timestamp, 5m)"
    },
    {
      title = "Jobs processed by outcome"
      query = "customMetrics | where name == 'codebreakers.jobs.processed' | extend outcome = tostring(customDimensions['codebreakers.job.outcome']) | summarize sum(valueSum) by bin(timestamp, 5m), outcome"
    },
    {
      title = "Job duration (average s)"
      query = "customMetrics | where name == 'codebreakers.jobs.duration' | summarize sum(valueSum) / sum(valueCount) by bin(timestamp, 5m)"
    },
    {
      title = "Retries and dead letters"
      query = "customMetrics | where name in ('codebreakers.jobs.retries', 'codebreakers.jobs.dead_lettered') | summarize sum(valueSum) by bin(timestamp, 5m), name"
    },
    {
      title = "Queue depth and oldest ready job age (s)"
      query = "customMetrics | where name in ('codebreakers.queue.depth', 'codebreakers.queue.oldest_age') | summarize max(valueMax) by bin(timestamp, 5m), name"
    },
  ]
}
