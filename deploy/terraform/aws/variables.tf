variable "name" {
  type        = string
  description = "Cell name; prefixes every resource (lowercase letters, digits, hyphens)."
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,30}$", var.name))
    error_message = "name must be 2-31 characters: lowercase letters, digits, hyphens, starting with a letter."
  }
}

variable "region" {
  type        = string
  description = "AWS region."
}

variable "vpc_cidr" {
  type    = string
  default = "10.40.0.0/16"
}

variable "az_count" {
  type    = number
  default = 3
}

variable "kubernetes_version" {
  type    = string
  default = "1.31"
}

variable "node_instance_types" {
  type    = list(string)
  default = ["m6i.xlarge"]
}

variable "node_min" {
  type    = number
  default = 2
}

variable "node_max" {
  type    = number
  default = 10
}

variable "cluster_public_access_cidrs" {
  type        = list(string)
  description = "CIDRs allowed to reach the Kubernetes API endpoint. Empty list = private endpoint only."
  default     = []
}

variable "db_instance_class" {
  type    = string
  default = "db.m6g.large"
}

variable "db_allocated_storage_gb" {
  type    = number
  default = 100
}

variable "db_multi_az" {
  type    = bool
  default = true
}

variable "db_deletion_protection" {
  type    = bool
  default = true
}

variable "bucket_force_destroy" {
  type        = bool
  description = "Allow terraform destroy to delete non-empty buckets. Keep false for anything real."
  default     = false
}

variable "kubernetes_namespace" {
  type    = string
  default = "substrate"
}

variable "release_name" {
  type        = string
  description = "Helm release name; the chart names service accounts after it."
  default     = "cell"
}

variable "tags" {
  type    = map(string)
  default = {}
}

variable "browser_origins" {
  type        = list(string)
  description = "Web origins of the cell's UIs (for direct browser uploads), e.g. [\"https://data.cell-1.example.com\"]."
}
