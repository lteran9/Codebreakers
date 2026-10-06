# Partial backend configuration for the dev state. Supply the bootstrap storage
# account at init: -backend-config=storage_account_name=<bootstrap output>.
resource_group_name = "rg-codebreakers-tfstate"
container_name      = "tfstate-dev"
key                 = "codebreakers.tfstate"
use_azuread_auth    = true
