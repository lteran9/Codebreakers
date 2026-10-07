# Outputs are identifiers only. Secrets stay in Key Vault and are never output.

output "resource_group_name" {
  description = "Resource group containing the environment."
  value       = azurerm_resource_group.main.name
}

output "api_url" {
  description = "Public HTTPS base URL of the API."
  value       = "https://${azurerm_container_app.app["api"].ingress[0].fqdn}"
}

output "container_registry_name" {
  description = "Registry name for `az acr build` / `az acr login`."
  value       = azurerm_container_registry.main.name
}

output "container_registry_login_server" {
  description = "Registry login server used in image references."
  value       = azurerm_container_registry.main.login_server
}

output "container_app_names" {
  description = "Container app name per role."
  value       = { for role, app in azurerm_container_app.app : role => app.name }
}

output "container_app_job_names" {
  description = "Container Apps job name per task (migrate, retention)."
  value       = { for task, job in azurerm_container_app_job.job : task => job.name }
}

output "migration_job_name" {
  description = "Manually triggered job that runs `alembic upgrade head`."
  value       = azurerm_container_app_job.job["migrate"].name
}

output "postgres_server_name" {
  description = "PostgreSQL Flexible Server name."
  value       = azurerm_postgresql_flexible_server.main.name
}

output "key_vault_name" {
  description = "Key Vault holding the database URL secret."
  value       = azurerm_key_vault.main.name
}

output "log_analytics_workspace_id" {
  description = "Log Analytics workspace (customer) ID for `az monitor log-analytics query`."
  value       = azurerm_log_analytics_workspace.main.workspace_id
}
