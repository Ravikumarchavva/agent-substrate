terraform {
  required_version = ">= 1.6"

  required_providers {
    keycloak = {
      source  = "keycloak/keycloak"
      version = "~> 5.0"
    }
  }
}

# Authenticates with a service account that may manage the realm (not the master admin user).
provider "keycloak" {
  url           = var.keycloak_url
  client_id     = var.terraform_client_id
  client_secret = var.terraform_client_secret
  realm         = "master"
}
