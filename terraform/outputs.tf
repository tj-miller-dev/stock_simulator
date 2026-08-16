output "cluster_name" {
  value = module.cluster.cluster_name
}

output "configure_kubectl" {
  description = "Run this after apply to point kubectl at the new cluster"
  value       = "aws eks update-kubeconfig --name ${module.cluster.cluster_name} --region ${var.aws_region}"
}

output "ecr_repository_urls" {
  value = module.registry.repository_urls
}

output "argocd_initial_admin_password_command" {
  description = "ArgoCD's auto-generated admin password lives in a secret -- run this to read it"
  value       = "kubectl -n ${module.gitops.namespace} get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d"
}
