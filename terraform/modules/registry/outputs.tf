output "repository_urls" {
  value = { for k, r in aws_ecr_repository.this : k => r.repository_url }
}

output "repository_arns" {
  description = "Used to scope the CI role's push permissions to just these repos"
  value       = [for r in aws_ecr_repository.this : r.arn]
}
