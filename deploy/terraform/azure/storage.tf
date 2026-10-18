resource "random_string" "suffix" {
  length  = 6
  upper   = false
  special = false
}

# One account, four containers. Containers are the buckets of the S3 mapping the cell uses (ABFS/S3 gateway).
resource "azurerm_storage_account" "this" {
  name                            = "${var.name}${random_string.suffix.result}"
  location                        = azurerm_resource_group.this.location
  resource_group_name             = azurerm_resource_group.this.name
  account_tier                    = "Standard"
  account_replication_type        = "ZRS"
  account_kind                    = "StorageV2"
  is_hns_enabled                  = true
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false
  shared_access_key_enabled       = false
  tags                            = local.tags

  blob_properties {
    versioning_enabled = true
    dynamic "cors_rule" {
      for_each = [1]
      content {
        allowed_origins    = var.browser_origins
        allowed_methods    = ["GET", "PUT", "POST", "HEAD"]
        allowed_headers    = ["*"]
        exposed_headers    = ["ETag"]
        max_age_in_seconds = 3600
      }
    }
  }

  network_rules {
    default_action             = "Deny"
    bypass                     = ["AzureServices"]
    virtual_network_subnet_ids = [azurerm_subnet.nodes.id]
  }
}

locals {
  containers = ["warehouse", "staging", "volumes", "agent-files"]
}

resource "azurerm_storage_container" "this" {
  for_each              = toset(local.containers)
  name                  = each.value
  storage_account_id    = azurerm_storage_account.this.id
  container_access_type = "private"
}

resource "azurerm_storage_management_policy" "staging" {
  storage_account_id = azurerm_storage_account.this.id
  rule {
    name    = "expire-staging"
    enabled = true
    filters {
      blob_types   = ["blockBlob"]
      prefix_match = ["staging/"]
    }
    actions {
      base_blob {
        delete_after_days_since_modification_greater_than = 7
      }
    }
  }
}
