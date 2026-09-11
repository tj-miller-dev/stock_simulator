output "public_ip" {
  description = "Elastic IP the DNS records point at, and the address you SSH to"
  value       = aws_eip.this.public_ip
}

output "ssh" {
  description = "Copy-paste shell access"
  value       = "ssh ec2-user@${aws_eip.this.public_ip}"
}

output "instance_id" {
  value = aws_instance.this.id
}

output "ecr_registry" {
  description = "Must match ECR_REGISTRY in .github/workflows/deploy.yml"
  value       = local.ecr_registry
}

output "github_actions_role_arn" {
  description = "Must match role-to-assume in .github/workflows/deploy.yml"
  value       = aws_iam_role.github_actions.arn
}

output "site_url" {
  value = "https://${var.domain_name}"
}
