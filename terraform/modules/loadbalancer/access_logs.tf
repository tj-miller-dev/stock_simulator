# Durable request log for the shared "stock-simulator" ALB.
#
# Why this exists: nothing else in this stack remembers a request. Pod stdout
# (nginx's and uvicorn's) is wiped by every deploy, restart, and node scale-down,
# and control-plane logging to CloudWatch is deliberately off. ALB access logs
# are the one record that survives, and they sit in front of *both* services --
# so API calls, which never touch nginx, are captured here or nowhere.
#
# The ALB itself is created by the AWS Load Balancer Controller from the
# annotations in k8s/ingress.yaml, not by Terraform, so this file only builds
# the bucket the ALB writes into. Enabling the feature is the
# access_logs.s3.* half of the load-balancer-attributes annotation there.

data "aws_caller_identity" "current" {}

locals {
  access_logs_bucket = "${var.cluster_name}-alb-logs-${data.aws_caller_identity.current.account_id}"

  # ELB writes under <prefix>/AWSLogs/<account>/... and the bucket policy has to
  # grant exactly that path. Keep in sync with access_logs.s3.prefix in
  # k8s/ingress.yaml.
  access_logs_prefix = "alb"

  # Regions that existed before August 2022 -- us-east-1 among them -- were
  # originally documented as granting log delivery to a per-region ELB *account*
  # rather than a service principal. AWS has since moved ALB delivery to the
  # logdelivery.elasticloadbalancing.amazonaws.com principal and both are honored,
  # but the guidance has flip-flopped enough that a wrong guess only surfaces as a
  # controller-side "Access Denied for bucket" at ingress-apply time. Granting both
  # costs one statement and can't misfire. Sourced from:
  # https://docs.aws.amazon.com/elasticloadbalancing/latest/application/enable-access-logging.html
  elb_log_delivery_accounts = {
    "us-east-1" = "127311923021"
    "us-east-2" = "033677994240"
    "us-west-1" = "027434742980"
    "us-west-2" = "797873946194"
    "eu-west-1" = "156460612806"
  }
  elb_log_delivery_account = lookup(local.elb_log_delivery_accounts, var.aws_region, null)
}

resource "aws_s3_bucket" "access_logs" {
  bucket = local.access_logs_bucket

  # Same reasoning as the ECR repos' force_delete: `terraform destroy` is a
  # documented, routine operation here, and it hard-fails on a bucket holding
  # objects. Without this the teardown in QUICK_START gains a manual step that
  # only bites you months later.
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "access_logs" {
  bucket = aws_s3_bucket.access_logs.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "access_logs" {
  bucket = aws_s3_bucket.access_logs.id

  # SSE-S3 specifically. ALB access log delivery supports SSE-S3 or SSE-KMS with
  # a customer-managed key, but *not* the default aws/s3 managed key -- picking
  # KMS here would mean also creating and granting a CMK for no benefit.
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "access_logs" {
  bucket = aws_s3_bucket.access_logs.id

  # At current volume (~1k requests/day) this bucket costs fractions of a cent a
  # month, but access logs are the classic object store that grows forever and is
  # never looked at past the first quarter. Bounded retention also bounds how long
  # client IPs are held.
  rule {
    id     = "expire-old-logs"
    status = "Enabled"

    filter {}

    expiration {
      days = var.access_logs_retention_days
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

data "aws_iam_policy_document" "access_logs" {
  statement {
    sid     = "ELBLogDeliveryServicePrincipal"
    effect  = "Allow"
    actions = ["s3:PutObject"]

    principals {
      type        = "Service"
      identifiers = ["logdelivery.elasticloadbalancing.amazonaws.com"]
    }

    resources = [
      "${aws_s3_bucket.access_logs.arn}/${local.access_logs_prefix}/AWSLogs/${data.aws_caller_identity.current.account_id}/*",
    ]

    condition {
      test     = "StringEquals"
      variable = "s3:x-amz-acl"
      values   = ["bucket-owner-full-control"]
    }
  }

  # The legacy grant -- see elb_log_delivery_accounts above. Skipped entirely in
  # regions where the account ID isn't mapped, which are the newer regions that
  # only ever supported the service principal anyway.
  dynamic "statement" {
    for_each = local.elb_log_delivery_account == null ? [] : [local.elb_log_delivery_account]

    content {
      sid     = "ELBLogDeliveryLegacyAccount"
      effect  = "Allow"
      actions = ["s3:PutObject"]

      principals {
        type        = "AWS"
        identifiers = ["arn:aws:iam::${statement.value}:root"]
      }

      resources = [
        "${aws_s3_bucket.access_logs.arn}/${local.access_logs_prefix}/AWSLogs/${data.aws_caller_identity.current.account_id}/*",
      ]
    }
  }
}

resource "aws_s3_bucket_policy" "access_logs" {
  bucket = aws_s3_bucket.access_logs.id
  policy = data.aws_iam_policy_document.access_logs.json

  # A policy applied while the public access block is still settling can be
  # rejected as public; ordering removes the race.
  depends_on = [aws_s3_bucket_public_access_block.access_logs]
}
