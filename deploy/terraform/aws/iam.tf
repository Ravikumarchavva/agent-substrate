# Three identities, least privilege, none with a long-lived key (ADR 0006: the control plane never touches data):
#   control  - the cell's service account: objects in warehouse, staging, volumes and agent files, and assume the vending role
#   vending  - assumed per statement by the control plane; the session policy narrows it to the files a query may read
#   (attached compute holds its own S3 identity in the cell Secret, or none when credential vending is on)
locals {
  oidc_host = replace(aws_iam_openid_connect_provider.this.url, "https://", "")
  sa_prefix = "system:serviceaccount:${var.kubernetes_namespace}"
}

data "aws_iam_policy_document" "irsa_assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.this.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_host}:sub"
      values   = ["${local.sa_prefix}:${var.release_name}"]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_host}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

# The chart runs every component under one service account (named after the release). Per-component accounts are a
# chart extension; until then this role carries the union of what the control plane and the agent server need.
data "aws_iam_policy_document" "control" {
  statement {
    sid     = "Objects"
    actions = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts", "s3:GetObjectVersion", "s3:DeleteObjectVersion"]
    resources = [
      for k in ["warehouse", "staging", "volumes", "agents"] : "${aws_s3_bucket.this[k].arn}/*"
    ]
  }
  statement {
    sid       = "Buckets"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation", "s3:ListBucketMultipartUploads", "s3:ListBucketVersions"]
    resources = [for k in ["warehouse", "staging", "volumes", "agents"] : aws_s3_bucket.this[k].arn]
  }
  statement {
    sid       = "Vend"
    actions   = ["sts:AssumeRole", "sts:TagSession"]
    resources = [aws_iam_role.vending.arn]
  }
}

resource "aws_iam_role" "control" {
  name               = "${var.name}-control"
  assume_role_policy = data.aws_iam_policy_document.irsa_assume.json
  tags               = local.tags
}

resource "aws_iam_role_policy" "control" {
  name   = "storage"
  role   = aws_iam_role.control.id
  policy = data.aws_iam_policy_document.control.json
}

# STS vending: the role the control plane assumes for each statement. Its own permissions are the ceiling (read and
# write on the warehouse, read on staging); the session policy passed at AssumeRole time narrows to exact files.
data "aws_iam_policy_document" "vending_assume" {
  statement {
    actions = ["sts:AssumeRole", "sts:TagSession"]
    principals {
      type        = "AWS"
      identifiers = [aws_iam_role.control.arn]
    }
  }
}

resource "aws_iam_role" "vending" {
  name                 = "${var.name}-storage-vending"
  assume_role_policy   = data.aws_iam_policy_document.vending_assume.json
  max_session_duration = 3600
  tags                 = local.tags
}

data "aws_iam_policy_document" "vending" {
  statement {
    actions   = ["s3:GetObject", "s3:PutObject", "s3:AbortMultipartUpload"]
    resources = ["${aws_s3_bucket.this["warehouse"].arn}/*"]
  }
  statement {
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.this["staging"].arn}/*"]
  }
  statement {
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [aws_s3_bucket.this["warehouse"].arn, aws_s3_bucket.this["staging"].arn]
  }
}

resource "aws_iam_role_policy" "vending" {
  name   = "ceiling"
  role   = aws_iam_role.vending.id
  policy = data.aws_iam_policy_document.vending.json
}
