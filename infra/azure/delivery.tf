# GitHub Actions deploys revisions with this identity via OpenID Connect
# (ADR-0013): no client secret exists, and only workflow jobs running in the
# named GitHub environment can exchange their token for it.
resource "azurerm_user_assigned_identity" "deploy" {
  name                = "id-${local.name}-deploy"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  tags                = local.tags
}

resource "azurerm_federated_identity_credential" "github" {
  name                      = "github-${var.github_environment}"
  user_assigned_identity_id = azurerm_user_assigned_identity.deploy.id
  issuer                    = "https://token.actions.githubusercontent.com"
  audience                  = ["api://AzureADTokenExchange"]
  subject                   = "repo:${var.github_repository}:environment:${var.github_environment}"
}

# Updating container apps and starting jobs needs write access to them;
# Contributor on this one resource group is the narrowest built-in role that
# covers Microsoft.App apps, jobs, and revisions. It cannot grant access.
resource "azurerm_role_assignment" "deploy_contributor" {
  scope                = azurerm_resource_group.main.id
  role_definition_name = "Contributor"
  principal_id         = azurerm_user_assigned_identity.deploy.principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "deploy_acr_push" {
  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPush"
  principal_id         = azurerm_user_assigned_identity.deploy.principal_id
  principal_type       = "ServicePrincipal"
}
