# Partial backend configuration for the prod state. Supply the bootstrap storage
# account at init: -backend-config=storage_account_name=<bootstrap output>.
resource_group_name = "rg-codebreakers-tfstate"
container_name      = "tfstate-prod"
key                 = "codebreakers.tfstate"
use_azuread_auth    = true
