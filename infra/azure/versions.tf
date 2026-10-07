terraform {
  required_version = ">= 1.16.0, < 2.0.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 5.8"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.9"
    }
    time = {
      source  = "hashicorp/time"
      version = "~> 0.14"
    }
  }

  # Partial configuration: environments/<env>.backend.hcl plus the bootstrap
  # storage account name supplied at `terraform init` (see the runbook).
  backend "azurerm" {}
}

# The subscription comes from ARM_SUBSCRIPTION_ID so no tenant-specific IDs are
# committed.
provider "azurerm" {
  storage_use_azuread = true

  features {
    key_vault {
      # Dev vaults are purged on destroy so they can be recreated immediately;
      # protected environments keep soft-deleted vaults recoverable.
      purge_soft_delete_on_destroy    = !var.protect_stateful_resources
      recover_soft_deleted_key_vaults = true
    }

    resource_group {
      # Application Insights creates a smart-detection alert rule outside
      # Terraform; dev teardown removes it with the group.
      prevent_deletion_if_contains_resources = var.protect_stateful_resources
    }
  }
}
