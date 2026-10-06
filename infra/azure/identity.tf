resource "azurerm_container_registry" "main" {
  #checkov:skip=CKV_AZURE_139:Basic SKU has no private endpoints; pulls require Microsoft Entra RBAC.
  #checkov:skip=CKV_AZURE_163:Image vulnerability scanning runs in the build pipeline (Trivy), not Defender.
  #checkov:skip=CKV_AZURE_164:Content trust (Notary v1) is retired; signing is a Phase 9 supply-chain task.
  #checkov:skip=CKV_AZURE_165:Geo-replication requires Premium SKU; not justified for this portfolio app.
  #checkov:skip=CKV_AZURE_166:Quarantine requires Premium SKU.
  #checkov:skip=CKV_AZURE_167:Untagged manifest retention requires Premium SKU.
  #checkov:skip=CKV_AZURE_233:Zone redundancy requires Premium SKU.
  #checkov:skip=CKV_AZURE_237:Dedicated data endpoints require Premium SKU.
  name                = "cr${local.unique}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  sku                 = "Basic"
  admin_enabled       = false
  tags                = local.tags
}

# One identity serves the API, relay, worker, and jobs: they run the same
# image and need exactly the same narrowly scoped permissions.
resource "azurerm_user_assigned_identity" "app" {
  name                = "id-${local.name}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  tags                = local.tags
}

resource "azurerm_role_assignment" "app_acr_pull" {
  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "app_telemetry" {
  scope                = azurerm_application_insights.main.id
  role_definition_name = "Monitoring Metrics Publisher"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
  principal_type       = "ServicePrincipal"
}

# Scoped to the single secret rather than the whole vault.
resource "azurerm_role_assignment" "app_database_url_reader" {
  scope                = azurerm_key_vault_secret.database_url.resource_versionless_id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
  principal_type       = "ServicePrincipal"
}

# Azure RBAC assignments take a short time to propagate; container apps that
# pull images and resolve secrets before then fail to provision.
resource "time_sleep" "app_rbac_propagation" {
  create_duration = "60s"

  depends_on = [
    azurerm_role_assignment.app_acr_pull,
    azurerm_role_assignment.app_telemetry,
    azurerm_role_assignment.app_database_url_reader,
  ]
}
