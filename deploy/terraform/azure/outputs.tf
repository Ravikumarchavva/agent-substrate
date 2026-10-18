output "cluster_name" {
  value = azurerm_kubernetes_cluster.this.name
}

output "kubeconfig_command" {
  value = "az aks get-credentials --resource-group ${azurerm_resource_group.this.name} --name ${azurerm_kubernetes_cluster.this.name}"
}

output "postgres_host" {
  value = azurerm_postgresql_flexible_server.this.fqdn
}

output "postgres_password" {
  description = "Put this in the cell Secret as postgres-password. It is also in the Terraform state: protect the state."
  value       = random_password.db.result
  sensitive   = true
}

output "storage_account" {
  value = azurerm_storage_account.this.name
}

output "workload_identity_client_id" {
  description = "Helm: serviceAccount.annotations[azure.workload.identity/client-id]"
  value       = azurerm_user_assigned_identity.cell.client_id
}

output "helm_values" {
  value = yamlencode({
    postgres       = { bundled = { enabled = false }, external = { host = azurerm_postgresql_flexible_server.this.fqdn, sslmode = "require" } }
    s3             = { bundled = { enabled = true } }
    serviceAccount = { annotations = { "azure.workload.identity/client-id" = azurerm_user_assigned_identity.cell.client_id } }
  })
}
