resource "random_password" "db" {
  length  = 32
  special = false
}

resource "google_sql_database_instance" "this" {
  name                = var.name
  project             = var.project_id
  region              = var.region
  database_version    = "POSTGRES_16"
  deletion_protection = var.db_deletion_protection

  settings {
    tier              = var.db_tier
    availability_type = var.db_availability_type
    disk_autoresize   = true
    user_labels       = local.labels

    ip_configuration {
      ipv4_enabled    = false
      private_network = google_compute_network.this.id
      # TLS is mandatory: loamz-data refuses an unencrypted database connection off the machine.
      ssl_mode = "ENCRYPTED_ONLY"
    }

    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = true
    }
  }

  depends_on = [google_service_networking_connection.private_services]
}

resource "google_sql_user" "loamz" {
  name     = "loamz"
  project  = var.project_id
  instance = google_sql_database_instance.this.name
  password = random_password.db.result
}

resource "google_sql_database" "this" {
  for_each = toset(["agentdb", "keycloak", "loamz_data", "loamz_ui"])
  name     = each.value
  project  = var.project_id
  instance = google_sql_database_instance.this.name
}
