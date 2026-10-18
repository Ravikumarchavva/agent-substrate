output "cluster_name" {
  value = google_container_cluster.this.name
}

output "kubeconfig_command" {
  value = "gcloud container clusters get-credentials ${google_container_cluster.this.name} --region ${var.region} --project ${var.project_id}"
}

output "postgres_host" {
  description = "Private IP of the Cloud SQL instance."
  value       = google_sql_database_instance.this.private_ip_address
}

output "postgres_password" {
  description = "Put this in the cell Secret as postgres-password. It is also in the Terraform state: protect the state."
  value       = random_password.db.result
  sensitive   = true
}

output "buckets" {
  value = local.buckets
}

output "vending_service_account" {
  value = google_service_account.vending.email
}

output "helm_values" {
  value = yamlencode({
    postgres = { bundled = { enabled = false }, external = { host = google_sql_database_instance.this.private_ip_address, sslmode = "require" } }
    s3 = {
      bundled  = { enabled = false }
      external = { endpoint = "https://storage.googleapis.com", region = "auto" }
      buckets  = local.buckets
    }
    serviceAccount = { annotations = { "iam.gke.io/gcp-service-account" = google_service_account.cell.email } }
  })
}
