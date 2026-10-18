# Workload identity: the cell's Kubernetes service account (named after the release) federates to a user-assigned
# managed identity. Storage roles are assigned per container, never on the account.
resource "azurerm_user_assigned_identity" "cell" {
  name                = "${var.name}-cell"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.tags
}

resource "azurerm_federated_identity_credential" "cell" {
  name                = "${var.name}-cell"
  resource_group_name = azurerm_resource_group.this.name
  parent_id           = azurerm_user_assigned_identity.cell.id
  audience            = ["api://AzureADTokenExchange"]
  issuer              = azurerm_kubernetes_cluster.this.oidc_issuer_url
  subject             = "system:serviceaccount:${var.kubernetes_namespace}:${var.release_name}"
}

resource "azurerm_role_assignment" "cell" {
  for_each             = azurerm_storage_container.this
  scope                = "${azurerm_storage_account.this.id}/blobServices/default/containers/${each.value.name}"
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.cell.principal_id
}

# Credential vending on Azure: the control plane requests user-delegation SAS tokens scoped to the files of one
# statement. That needs the delegator role at account level and nothing else. substrate-data's vending speaks AWS STS
# today; until an Azure provider lands, workers use the identity above directly.
resource "azurerm_role_assignment" "delegator" {
  scope                = azurerm_storage_account.this.id
  role_definition_name = "Storage Blob Delegator"
  principal_id         = azurerm_user_assigned_identity.cell.principal_id
}
