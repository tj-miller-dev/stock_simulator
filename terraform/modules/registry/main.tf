resource "aws_ecr_repository" "this" {
  for_each = toset(var.repository_names)

  name                 = each.value
  image_tag_mutability = "IMMUTABLE" # forces real version bumps (or git-sha tags) instead of overwriting e.g. v0.2 in place

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
      # CI tags every build with its commit SHA, so nothing is ever overwritten
      # and the untagged rule above would never catch anything. Without this
      # cap the repos grow by one image per commit, forever.
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
