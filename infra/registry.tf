data "aws_caller_identity" "current" {}

locals {
  ecr_registry = "${data.aws_caller_identity.current.account_id}.dkr.ecr.${var.aws_region}.amazonaws.com"

  services = ["stock-simulator-api", "stock-simulator-frontend"]
}

resource "aws_ecr_repository" "this" {
  for_each = toset(local.services)

  name = each.value

  # MUTABLE, where the EKS stack used IMMUTABLE. The box pulls a fixed tag on a
  # timer, so something has to mean "current" -- CI pushes :latest alongside the
  # commit-SHA tag, and :latest has to be allowed to move. The SHA tag is still
  # pushed and still immutable in practice (nothing ever overwrites one), so
  # provenance and rollback are unchanged: pin IMAGE_TAG in deploy/.env to any
  # SHA to go back.
  image_tag_mutability = "MUTABLE"

  # CI pushes a new tag per commit, so emptying these by hand before a destroy
  # (deleting each tag individually) stops being practical fast.
  force_delete = true

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "this" {
  for_each   = aws_ecr_repository.this
  repository = each.value.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Expire untagged images after 7 days"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 7
        }
        action = { type = "expire" }
      },
      # Every build adds an image that keeps its SHA tag forever, so the untagged
      # rule above would never catch them. Without this cap the repos grow by one
      # image per commit, indefinitely. Twenty is far more history than a rollback
      # has ever needed here.
      {
        rulePriority = 2
        description  = "Keep only the 20 most recent images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 20
        }
        action = { type = "expire" }
      },
    ]
  })
}
