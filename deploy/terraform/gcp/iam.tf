# The cell's service account (the chart runs every component under one, named after the release) acts as a Google
# service account through Workload Identity: no keys. Object access is granted per bucket, never project-wide.
resource "google_service_account" "cell" {
  project      = var.project_id
  account_id   = "${var.name}-cell"
  display_name = "Substrate cell ${var.name}"
}

resource "google_service_account_iam_member" "workload_identity" {
  service_account_id = google_service_account.cell.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[${var.kubernetes_namespace}/${var.release_name}]"
}

resource "google_storage_bucket_iam_member" "cell" {
  for_each = google_storage_bucket.this
  bucket   = each.value.name
  role     = "roles/storage.objectAdmin"
  member   = "serviceAccount:${google_service_account.cell.email}"
}

# Credential vending on Google Cloud: the control plane impersonates this account to mint short-lived, downscoped
# tokens (Credential Access Boundaries) for attached compute. loamz-data's vending speaks AWS STS today, so
# until a GCS provider lands, workers on GCP use the S3-interoperability endpoint with HMAC keys kept in the cell Secret.
resource "google_service_account" "vending" {
  project      = var.project_id
  account_id   = "${var.name}-vending"
  display_name = "Downscoped storage access for cell ${var.name}"
}

resource "google_storage_bucket_iam_member" "vending_warehouse" {
  bucket = google_storage_bucket.this["warehouse"].name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.vending.email}"
}

resource "google_storage_bucket_iam_member" "vending_staging" {
  bucket = google_storage_bucket.this["staging"].name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.vending.email}"
}

resource "google_service_account_iam_member" "vending_impersonation" {
  service_account_id = google_service_account.vending.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_service_account.cell.email}"
}
