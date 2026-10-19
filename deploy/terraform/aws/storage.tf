locals {
  buckets = {
    warehouse = "${var.name}-warehouse"
    staging   = "${var.name}-staging"
    volumes   = "${var.name}-volumes"
    agents    = "${var.name}-agent-files"
  }
}

resource "aws_s3_bucket" "this" {
  for_each      = local.buckets
  bucket        = each.value
  force_destroy = var.bucket_force_destroy
  tags          = merge(local.tags, { "loamz:role" = each.key })
}

resource "aws_s3_bucket_public_access_block" "this" {
  for_each                = aws_s3_bucket.this
  bucket                  = each.value.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  for_each = aws_s3_bucket.this
  bucket   = each.value.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "volumes" {
  bucket = aws_s3_bucket.this["volumes"].id
  versioning_configuration {
    status = "Enabled"
  }
}

# Uploads that never completed, and staged files, do not live forever.
resource "aws_s3_bucket_lifecycle_configuration" "staging" {
  bucket = aws_s3_bucket.this["staging"].id
  rule {
    id     = "expire-staging"
    status = "Enabled"
    filter {}
    expiration {
      days = 7
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 2
    }
  }
}

# Browsers upload straight to presigned URLs.
resource "aws_s3_bucket_cors_configuration" "volumes" {
  bucket = aws_s3_bucket.this["volumes"].id
  cors_rule {
    allowed_methods = ["GET", "PUT", "POST", "HEAD"]
    allowed_origins = var.browser_origins
    allowed_headers = ["*"]
    expose_headers  = ["ETag"]
    max_age_seconds = 3600
  }
}
