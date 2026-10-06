# URL-safe characters only: the password is embedded in the database URL.
resource "random_password" "postgres" {
  length  = 32
  special = false
}

resource "azurerm_postgresql_flexible_server" "main" {
  #checkov:skip=CKV_AZURE_136:Geo-redundant backups double backup cost; dev data is disposable (ADR-0012).
  #checkov:skip=CKV2_AZURE_57:Private access needs VNet integration, out of scope for the dev environment (ADR-0012).
  name                          = "psql-${local.unique}"
  location                      = azurerm_resource_group.main.location
  resource_group_name           = azurerm_resource_group.main.name
  version                       = "16"
  sku_name                      = var.postgres_sku_name
  storage_mb                    = var.postgres_storage_mb
  backup_retention_days         = var.postgres_backup_retention_days
  geo_redundant_backup_enabled  = false
  public_network_access_enabled = true
  administrator_login           = "codebreakers"
  administrator_password        = random_password.postgres.result
  tags                          = local.tags

  authentication {
    password_auth_enabled         = true
    active_directory_auth_enabled = false
  }

  lifecycle {
    # Azure picks a zone when none is requested; do not churn on it.
    ignore_changes = [zone]
  }
}

resource "azurerm_postgresql_flexible_server_database" "app" {
  name      = "codebreakers"
  server_id = azurerm_postgresql_flexible_server.main.id
  charset   = "UTF8"
  collation = "en_US.utf8"
}

# 0.0.0.0 is Azure's "allow Azure services" rule: Container Apps on the
# consumption plan have no fixed outbound address. TLS and the password still
# apply; see the trust boundaries in ADR-0012.
resource "azurerm_postgresql_flexible_server_firewall_rule" "azure_services" {
  #checkov:skip=CKV2_AZURE_26:Consumption Container Apps lack fixed egress IPs; TLS plus password apply (ADR-0012).
  name             = "allow-azure-services"
  server_id        = azurerm_postgresql_flexible_server.main.id
  start_ip_address = "0.0.0.0"
  end_ip_address   = "0.0.0.0"
}

resource "azurerm_management_lock" "database" {
  count = var.protect_stateful_resources ? 1 : 0

  name       = "protect-database"
  scope      = azurerm_postgresql_flexible_server.main.id
  lock_level = "CanNotDelete"
  notes      = "Remove this lock deliberately before destroying the production database."
}
