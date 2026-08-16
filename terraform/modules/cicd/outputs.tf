output "github_actions_role_arn" {
  description = "Role ARN for the workflow's aws-actions/configure-aws-credentials step"
  value       = aws_iam_role.github_actions.arn
}
