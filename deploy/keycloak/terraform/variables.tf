variable "keycloak_url" {
  type        = string
  description = "Base URL of Keycloak, e.g. https://id.cell-1.example.com"
}

variable "terraform_client_id" {
  type    = string
  default = "terraform"
}

variable "terraform_client_secret" {
  type      = string
  sensitive = true
}

variable "realm" {
  type    = string
  default = "substrate"
}

variable "ui_url" {
  type        = string
  description = "Origin of the agents console (substrate-ui)."
  default     = "http://127.0.0.1:3000"
}

variable "data_web_url" {
  type        = string
  description = "Origin of the data workspace (substrate-data-web)."
  default     = "http://127.0.0.1:3011"
}

variable "ui_client_secret" {
  type      = string
  sensitive = true
}

variable "data_web_client_secret" {
  type      = string
  sensitive = true
}

variable "cell_admin_client_secret" {
  type      = string
  sensitive = true
}
