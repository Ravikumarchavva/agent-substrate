resource "azurerm_kubernetes_cluster" "this" {
  name                = var.name
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  dns_prefix          = var.name
  kubernetes_version  = var.kubernetes_version
  sku_tier            = "Standard"

  # Pods act as Entra identities without secrets (workload identity).
  oidc_issuer_enabled       = true
  workload_identity_enabled = true

  default_node_pool {
    name                 = "default"
    vm_size              = var.node_vm_size
    vnet_subnet_id       = azurerm_subnet.nodes.id
    auto_scaling_enabled = true
    min_count            = var.node_min
    max_count            = var.node_max
    zones                = ["1", "2", "3"]
    upgrade_settings {
      max_surge = "33%"
    }
  }

  identity {
    type = "SystemAssigned"
  }

  network_profile {
    network_plugin    = "azure"
    network_policy    = "calico"
    load_balancer_sku = "standard"
    service_cidr      = "10.71.0.0/16"
    dns_service_ip    = "10.71.0.10"
  }

  dynamic "api_server_access_profile" {
    for_each = length(var.api_server_authorized_cidrs) > 0 ? [1] : []
    content {
      authorized_ip_ranges = var.api_server_authorized_cidrs
    }
  }

  tags = local.tags
}
