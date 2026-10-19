resource "random_password" "db" {
  length  = 32
  special = false
}

resource "azurerm_postgresql_flexible_server" "this" {
  name                          = "${var.name}-pg"
  location                      = azurerm_resource_group.this.location
  resource_group_name           = azurerm_resource_group.this.name
  version                       = "16"
  sku_name                      = var.db_sku
  storage_mb                    = var.db_storage_mb
  delegated_subnet_id           = azurerm_subnet.db.id
  private_dns_zone_id           = azurerm_private_dns_zone.db.id
  public_network_access_enabled = false
  administrator_login           = "loamz"
  administrator_password        = random_password.db.result
  backup_retention_days         = 14
  zone                          = "1"
  tags                          = local.tags

  dynamic "high_availability" {
    for_each = var.db_high_availability ? [1] : []
    content {
      mode                      = "ZoneRedundant"
      standby_availability_zone = "2"
    }
  }

  depends_on = [azurerm_private_dns_zone_virtual_network_link.db]

  lifecycle {
    ignore_changes = [zone, high_availability[0].standby_availability_zone]
  }
}

# TLS is mandatory: loamz-data refuses an unencrypted database connection off the machine.
resource "azurerm_postgresql_flexible_server_configuration" "require_tls" {
  name      = "require_secure_transport"
  server_id = azurerm_postgresql_flexible_server.this.id
  value     = "on"
}

resource "azurerm_postgresql_flexible_server_configuration" "extensions" {
  name      = "azure.extensions"
  server_id = azurerm_postgresql_flexible_server.this.id
  value     = "VECTOR,UUID-OSSP"
}

resource "azurerm_postgresql_flexible_server_database" "this" {
  for_each  = toset(["agentdb", "keycloak", "loamz_data", "loamz_ui"])
  name      = each.value
  server_id = azurerm_postgresql_flexible_server.this.id
}
