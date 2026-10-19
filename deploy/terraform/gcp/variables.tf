variable "name" {
  type        = string
  description = "Cell name; prefixes every resource (lowercase letters, digits, hyphens)."
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,24}$", var.name))
    error_message = "name must be 2-25 characters: lowercase letters, digits, hyphens, starting with a letter."
  }
}

variable "project_id" {
  type = string
}

variable "region" {
  type = string
}

variable "subnet_cidr" {
  type    = string
  default = "10.50.0.0/20"
}

variable "pods_cidr" {
  type    = string
  default = "10.60.0.0/14"
}

variable "services_cidr" {
  type    = string
  default = "10.64.0.0/20"
}

variable "master_authorized_cidrs" {
  type        = map(string)
  description = "Name => CIDR allowed to reach the Kubernetes API."
  default     = {}
}

variable "node_machine_type" {
  type    = string
  default = "e2-standard-4"
}

variable "node_min" {
  type    = number
  default = 1
}

variable "node_max" {
  type    = number
  default = 10
}

variable "db_tier" {
  type    = string
  default = "db-custom-2-7680"
}

variable "db_availability_type" {
  type    = string
  default = "REGIONAL"
}

variable "db_deletion_protection" {
  type    = bool
  default = true
}

variable "bucket_force_destroy" {
  type    = bool
  default = false
}

variable "browser_origins" {
  type        = list(string)
  description = "Web origins of the cell's UIs (for direct browser uploads)."
}

variable "kubernetes_namespace" {
  type    = string
  default = "loamz"
}

variable "release_name" {
  type    = string
  default = "cell"
}

variable "labels" {
  type    = map(string)
  default = {}
}
