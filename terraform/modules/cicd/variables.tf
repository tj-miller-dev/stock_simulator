variable "cluster_name" {
  description = "Name prefix for the CI role"
  type        = string
}

variable "github_repository" {
  description = "GitHub repository (owner/name) whose workflows may assume the CI role"
  type        = string
}

variable "github_repository_immutable" {
  description = <<-EOT
    Same repo in GitHub's immutable-subject form, owner@ownerid/name@repoid. This is
    what actually appears in the OIDC token's `sub` claim. Read it with:
      gh api repos/OWNER/NAME/actions/oidc/customization/sub -q .sub_claim_prefix
  EOT
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
