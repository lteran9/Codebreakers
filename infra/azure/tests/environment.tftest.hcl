# Credential-free checks of the environment configuration. Run against each
# environment's inputs (see `make tf-test`):
#   terraform test -var-file=environments/dev.tfvars
#   terraform test -var-file=environments/prod.tfvars

mock_provider "azurerm" {
  mock_data "azurerm_client_config" {
    defaults = {
      tenant_id       = "00000000-0000-0000-0000-000000000001"
      object_id       = "00000000-0000-0000-0000-000000000002"
      subscription_id = "00000000-0000-0000-0000-000000000003"
      client_id       = "00000000-0000-0000-0000-000000000004"
    }
  }

  mock_resource "azurerm_user_assigned_identity" {
    defaults = {
      id           = "/subscriptions/00000000-0000-0000-0000-000000000003/resourceGroups/rg-test/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-test"
      principal_id = "00000000-0000-0000-0000-000000000005"
      client_id    = "00000000-0000-0000-0000-000000000006"
    }
  }

  mock_resource "azurerm_resource_group" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000003/resourceGroups/rg-test"
    }
  }

  mock_resource "azurerm_log_analytics_workspace" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000003/resourceGroups/rg-test/providers/Microsoft.OperationalInsights/workspaces/log-test"
    }
  }

  mock_resource "azurerm_application_insights" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000003/resourceGroups/rg-test/providers/Microsoft.Insights/components/appi-test"
    }
  }

  mock_resource "azurerm_container_registry" {
    defaults = {
      id           = "/subscriptions/00000000-0000-0000-0000-000000000003/resourceGroups/rg-test/providers/Microsoft.ContainerRegistry/registries/crtest"
      login_server = "crtest.azurecr.io"
    }
  }

  mock_resource "azurerm_key_vault" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000003/resourceGroups/rg-test/providers/Microsoft.KeyVault/vaults/kv-test"
    }
  }

  mock_resource "azurerm_key_vault_secret" {
    defaults = {
      versionless_id          = "https://kv-test.vault.azure.net/secrets/database-url"
      resource_versionless_id = "/subscriptions/00000000-0000-0000-0000-000000000003/resourceGroups/rg-test/providers/Microsoft.KeyVault/vaults/kv-test/secrets/database-url"
    }
  }

  mock_resource "azurerm_postgresql_flexible_server" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000003/resourceGroups/rg-test/providers/Microsoft.DBforPostgreSQL/flexibleServers/psql-test"
    }
  }

  mock_resource "azurerm_container_app_environment" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000003/resourceGroups/rg-test/providers/Microsoft.App/managedEnvironments/cae-test"
    }
  }
}

mock_provider "random" {}

mock_provider "time" {
  mock_resource "time_static" {
    defaults = {
      rfc3339 = "2026-10-06T12:00:00Z"
    }
  }
}

variables {
  image_tag             = "0123456789abcdef0123456789abcdef01234567"
  budget_contact_emails = ["alerts@example.com"]
}

run "environment" {
  command = apply

  assert {
    condition     = toset(keys(azurerm_container_app.app)) == toset(["api", "relay", "worker"])
    error_message = "Expected api, relay, and worker container apps."
  }

  assert {
    condition = alltrue([
      for app in azurerm_container_app.app :
      app.template[0].container[0].image == "${azurerm_container_registry.main.login_server}/codebreakers:${var.image_tag}"
    ])
    error_message = "Every app must run the same versioned image."
  }

  assert {
    condition = alltrue([
      for role, app in azurerm_container_app.app : (length(app.ingress) == 1) == (role == "api")
    ])
    error_message = "Only the API may have ingress."
  }

  assert {
    condition = (
      azurerm_container_app.app["api"].ingress[0].external_enabled &&
      !azurerm_container_app.app["api"].ingress[0].allow_insecure_connections
    )
    error_message = "The API must be public over HTTPS only."
  }

  assert {
    condition     = length(azurerm_container_app.app["api"].template[0].http_scale_rule) == 1
    error_message = "The API must scale on HTTP concurrency."
  }

  assert {
    condition = (
      azurerm_container_app.app["worker"].template[0].custom_scale_rule[0].custom_rule_type == "postgresql" &&
      strcontains(azurerm_container_app.app["worker"].template[0].custom_scale_rule[0].metadata.query, "analysis_job_queue")
    )
    error_message = "The worker must scale on PostgreSQL queue depth."
  }

  assert {
    condition     = azurerm_container_app.app["worker"].template[0].termination_grace_period_seconds > 30
    error_message = "Worker shutdown grace must exceed the 30 s analysis budget."
  }

  assert {
    condition = alltrue(flatten([
      for app in azurerm_container_app.app : [
        for env in app.template[0].container[0].env :
        env.secret_name == "database-url" if env.name == "CODEBREAKERS_DATABASE_URL"
      ]
    ]))
    error_message = "The database URL must come from the Key Vault-backed secret."
  }

  assert {
    condition = alltrue([
      for app in azurerm_container_app.app :
      one(app.registry).identity == azurerm_user_assigned_identity.app.id && one(app.secret).identity == azurerm_user_assigned_identity.app.id
    ])
    error_message = "Registry pulls and secret reads must use the managed identity."
  }

  assert {
    condition     = !azurerm_container_registry.main.admin_enabled
    error_message = "Registry admin (password) access must stay disabled."
  }

  assert {
    condition     = !azurerm_application_insights.main.local_authentication_enabled
    error_message = "Telemetry ingestion must require Microsoft Entra authentication."
  }

  assert {
    condition     = azurerm_key_vault.main.rbac_authorization_enabled
    error_message = "Key Vault must use Azure RBAC."
  }

  assert {
    condition     = azurerm_role_assignment.app_database_url_reader.scope == azurerm_key_vault_secret.database_url.resource_versionless_id
    error_message = "Secret read access must be scoped to the single secret."
  }

  assert {
    condition     = join(" ", azurerm_container_app_job.job["migrate"].template[0].container[0].command) == "alembic"
    error_message = "The migration job must run Alembic."
  }

  assert {
    condition     = length(azurerm_container_app_job.job["retention"].schedule_trigger_config) == 1
    error_message = "Retention must run on a schedule."
  }

  assert {
    condition     = length(azurerm_management_lock.database) == (var.protect_stateful_resources ? 1 : 0)
    error_message = "Protected environments must lock the database against deletion."
  }

  assert {
    condition     = azurerm_key_vault.main.purge_protection_enabled == var.protect_stateful_resources
    error_message = "Purge protection must follow protect_stateful_resources."
  }

  assert {
    condition = alltrue([
      for key in ["application", "environment", "owner", "cost_center"] :
      contains(keys(azurerm_resource_group.main.tags), key)
    ])
    error_message = "Resources must carry application, environment, owner, and cost_center tags."
  }

  assert {
    condition     = startswith(output.api_url, "https://")
    error_message = "The API URL output must be HTTPS."
  }
}

run "rejects_latest_image_tag" {
  command = plan

  variables {
    image_tag = "latest"
  }

  expect_failures = [var.image_tag]
}
