resource "azurerm_key_vault" "main" {
  #checkov:skip=CKV_AZURE_109:Public endpoint is RBAC-only; private endpoints need VNet integration not used here (ADR-0012).
  #checkov:skip=CKV_AZURE_189:Public endpoint is required for Terraform and Container Apps on the consumption plan.
  #checkov:skip=CKV2_AZURE_32:Private endpoints are out of scope for the dev environment (ADR-0012).
  name                          = "kv-${local.unique}"
  location                      = azurerm_resource_group.main.location
  resource_group_name           = azurerm_resource_group.main.name
  tenant_id                     = data.azurerm_client_config.current.tenant_id
  sku_name                      = "standard"
  rbac_authorization_enabled    = true
  soft_delete_retention_days    = 7
  purge_protection_enabled      = var.protect_stateful_resources
  public_network_access_enabled = true
  tags                          = local.tags

  network_acls {
    default_action = "Allow"
    bypass         = "AzureServices"
  }
}

# The identity running Terraform writes secrets; applications only read them.
resource "azurerm_role_assignment" "deployer_secrets_officer" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "time_sleep" "deployer_rbac_propagation" {
  create_duration = "60s"

  depends_on = [azurerm_role_assignment.deployer_secrets_officer]
}

# PostgreSQL password authentication is the one credential that does not use
# managed identity in this phase (ADR-0012). The URL is read from Key Vault by
# Container Apps and the KEDA scale rules; it is never a Terraform output.
resource "azurerm_key_vault_secret" "database_url" {
  #checkov:skip=CKV_AZURE_41:Rotation is manual (bump the password and re-apply); no fixed expiry.
  name         = "database-url"
  key_vault_id = azurerm_key_vault.main.id
  content_type = "text/uri"
  value = format(
    "postgresql://%s:%s@%s:5432/%s?sslmode=require",
    azurerm_postgresql_flexible_server.main.administrator_login,
    random_password.postgres.result,
    azurerm_postgresql_flexible_server.main.fqdn,
    azurerm_postgresql_flexible_server_database.app.name,
  )
  tags = local.tags

  depends_on = [time_sleep.deployer_rbac_propagation]
}
