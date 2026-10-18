variable "name" {
  type        = string
  description = "Cell name; prefixes every resource (lowercase letters and digits, 3-14 characters; storage account names forbid hyphens)."
  validation {
    condition     = can(regex("^[a-z][a-z0-9]{2,13}$", var.name))
    error_message = "name must be 3-14 characters: lowercase letters and digits, starting with a letter."
  }
}

variable "location" {
  type = string
}

variable "vnet_cidr" {
  type    = string
  default = "10.70.0.0/16"
}

variable "kubernetes_version" {
  type    = string
  default = null
}

variable "node_vm_size" {
  type    = string
  default = "Standard_D4s_v5"
}

variable "node_min" {
  type    = number
  default = 2
}

variable "node_max" {
  type    = number
  default = 10
}

variable "api_server_authorized_cidrs" {
  type    = list(string)
  default = []
}

variable "db_sku" {
  type    = string
  default = "GP_Standard_D2s_v3"
}

variable "db_storage_mb" {
  type    = number
  default = 131072
}

variable "db_high_availability" {
  type    = bool
  default = true
}

variable "browser_origins" {
  type        = list(string)
  description = "Web origins of the cell's UIs (for direct browser uploads)."
}

variable "kubernetes_namespace" {
  type    = string
  default = "substrate"
}

variable "release_name" {
  type    = string
  default = "cell"
}

variable "tags" {
  type    = map(string)
  default = {}
}
