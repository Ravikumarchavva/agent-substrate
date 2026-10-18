output "cluster_name" {
  value = aws_eks_cluster.this.name
}

output "kubeconfig_command" {
  value = "aws eks update-kubeconfig --region ${var.region} --name ${aws_eks_cluster.this.name}"
}

output "postgres_host" {
  value = aws_db_instance.this.address
}

output "postgres_master_secret_arn" {
  description = "Secrets Manager secret holding the master password (copy it into the cell Secret as postgres-password)."
  value       = aws_db_instance.this.master_user_secret[0].secret_arn
}

output "buckets" {
  value = local.buckets
}

output "sts_vending_role_arn" {
  description = "Helm: s3.stsRoleArn"
  value       = aws_iam_role.vending.arn
}

output "control_role_arn" {
  description = "Helm: serviceAccount.annotations[eks.amazonaws.com/role-arn]"
  value       = aws_iam_role.control.arn
}

# Everything the chart needs, as Helm values.
output "helm_values" {
  value = yamlencode({
    postgres = { bundled = { enabled = false }, external = { host = aws_db_instance.this.address, sslmode = "require" } }
    redis    = { bundled = { enabled = true } }
    s3 = {
      bundled = { enabled = false }
      external = {
        endpoint          = "https://s3.${var.region}.amazonaws.com"
        region            = var.region
        icebergCatalogUrl = "https://glue.${var.region}.amazonaws.com/iceberg"
      }
      buckets    = { for k, v in local.buckets : k => v }
      stsRoleArn = aws_iam_role.vending.arn
    }
    serviceAccount = { annotations = { "eks.amazonaws.com/role-arn" = aws_iam_role.control.arn } }
  })
}
