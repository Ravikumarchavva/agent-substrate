locals {
  buckets = {
    warehouse = "${var.project_id}-${var.name}-warehouse"
    staging   = "${var.project_id}-${var.name}-staging"
    volumes   = "${var.project_id}-${var.name}-volumes"
    agents    = "${var.project_id}-${var.name}-agent-files"
  }
}

resource "google_storage_bucket" "this" {
  for_each                    = local.buckets
  name                        = each.value
  project                     = var.project_id
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = var.bucket_force_destroy
  labels                      = merge(local.labels, { loamz-role = each.key })

  dynamic "versioning" {
    for_each = each.key == "volumes" ? [1] : []
    content {
      enabled = true
    }
  }

  dynamic "lifecycle_rule" {
    for_each = each.key == "staging" ? [1] : []
    content {
      condition {
        age = 7
      }
      action {
        type = "Delete"
      }
    }
  }

  dynamic "cors" {
    for_each = each.key == "volumes" ? [1] : []
    content {
      origin          = var.browser_origins
      method          = ["GET", "PUT", "POST", "HEAD"]
      response_header = ["ETag", "Content-Type"]
      max_age_seconds = 3600
    }
  }
}
