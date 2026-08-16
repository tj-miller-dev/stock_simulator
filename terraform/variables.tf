variable "aws_access_key" {
  description = "AWS access key used by the provider. Set in terraform.tfvars, never commit it."
  type        = string
  sensitive   = true
}

variable "aws_secret_key" {
  description = "AWS secret key used by the provider. Set in terraform.tfvars, never commit it."
  type        = string
  sensitive   = true
}

variable "aws_region" {
  description = "AWS region to deploy into"
  type        = string
  default     = "us-east-1"
}

variable "cluster_name" {
  description = "Name of the EKS cluster"
  type        = string
  default     = "stock-simulator"
}

variable "kubernetes_version" {
  description = "EKS control plane version. Verify this is still on standard support before applying: https://docs.aws.amazon.com/eks/latest/userguide/kubernetes-versions-standard.html"
  type        = string
  default     = "1.33"
}

variable "vpc_cidr" {
  description = "CIDR block for the cluster VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "node_instance_types" {
  description = "Instance types for the default managed node group"
  type        = list(string)
  default     = ["t3.medium"]
}

variable "node_min_size" {
  type    = number
  default = 1
}

variable "node_max_size" {
  type    = number
  default = 4
}

variable "node_desired_size" {
  type    = number
  default = 2
}

variable "argocd_namespace" {
  type    = string
  default = "argocd"
}

variable "argocd_chart_version" {
  description = "Version of the argo/argo-cd Helm chart. Check https://artifacthub.io/packages/helm/argo/argo-cd for newer releases."
  type        = string
  default     = "10.3.3"
}

variable "github_repository" {
  description = "GitHub repository (owner/name) whose deploy workflow may push to ECR"
  type        = string
  default     = "tj-miller-dev/stock_simulator"
}

variable "deploy_branch" {
  description = "Branch that builds and deploys. Must match the targetRevision ArgoCD tracks in argocd/root-app.yaml."
  type        = string
  default     = "main"
}
