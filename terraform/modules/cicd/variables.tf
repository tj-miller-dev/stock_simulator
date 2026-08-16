variable "cluster_name" {
  description = "Name prefix for the CI role"
  type        = string
}

variable "github_repository" {
  description = "GitHub repository (owner/name) whose workflows may assume the CI role"
  type        = string
}

variable "deploy_branch" {
  description = "Branch whose pushes may assume the CI role. Must match the targetRevision ArgoCD tracks in argocd/root-app.yaml."
  type        = string
}

variable "ecr_repository_arns" {
  description = "ECR repositories the workflow is allowed to push to"
  type        = list(string)
}
