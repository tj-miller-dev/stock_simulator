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

variable "name" {
  description = "Name prefix for every resource in this stack"
  type        = string
  default     = "cuckootrade"
}

variable "domain_name" {
  description = "Apex domain (registered/hosted in Route53) the app is served from"
  type        = string
  default     = "cuckootrade.com"
}

variable "ssh_allowed_cidrs" {
  description = <<-EOT
    CIDR blocks allowed to reach port 22 -- e.g. your IP as a /32. This is the only
    non-public port on the box. If your home IP changes you must update this and
    re-apply, or you lose SSH (nothing else breaks: the site keeps serving, and the
    update timer keeps deploying, because neither needs you logged in).
    Find your current IP with: curl https://checkip.amazonaws.com
  EOT
  type        = list(string)
  sensitive   = true
}

variable "ssh_public_key" {
  description = <<-EOT
    Contents of the public half of the SSH key you'll log in with, e.g. the text of
    ~/.ssh/id_ed25519.pub. Only the public half -- Terraform never sees, and never
    stores in state, a private key. Generate one with:
      ssh-keygen -t ed25519 -C cuckootrade
  EOT
  type        = string
}

variable "instance_type" {
  description = <<-EOT
    Burstable x86 instance. t3.micro (2 vCPU, 1 GiB) is sized for the real traffic --
    a couple of human visitors a day plus health checks -- not for headroom. The 1 GiB
    is the binding constraint, not the CPU: user_data adds 2 GiB of swap so a docker
    pull or a numpy import spike can't OOM the box. Step up to t3.small if you ever
    see the swap actually being used under normal serving.
  EOT
  type        = string
  default     = "t3.micro"
}

variable "root_volume_size" {
  description = "Root EBS volume in GiB. Holds the OS plus a handful of container images; the update script prunes dangling layers each tick."
  type        = number
  default     = 12
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC. One public subnet is carved out of it; there are no private subnets and no NAT gateway."
  type        = string
  default     = "10.0.0.0/16"
}

variable "github_repository" {
  description = "GitHub repository (owner/name) whose deploy workflow may push to ECR"
  type        = string
  default     = "tj-miller-dev/stock_simulator"
}

variable "github_repository_immutable" {
  description = <<-EOT
    Same repo in GitHub's immutable-subject form (owner@ownerid/name@repoid), which is
    what the OIDC token's `sub` claim actually contains. Re-read it with:
      gh api repos/OWNER/NAME/actions/oidc/customization/sub -q .sub_claim_prefix
  EOT
  type        = string
  default     = "tj-miller-dev@204254190/stock_simulator@1335562161"
}

variable "deploy_branch" {
  description = <<-EOT
    Branch that builds, pushes images, and is cloned onto the box. NOT `main`:
    `main` is kept as the EKS/ArgoCD portfolio branch and no longer deploys anything.
    Three things must agree on this value -- this variable (which writes the CI role's
    trust policy), the `on.push.branches` filter in .github/workflows/deploy.yml, and
    the branch actually checked out in /opt/cuckootrade on the instance. Change it in
    all three or deploys stop landing.
  EOT
  type        = string
  default     = "simplify_deployment"
}

variable "repo_url" {
  description = "Clone URL for the deploy branch. Public over HTTPS, so the box needs no git credentials."
  type        = string
  default     = "https://github.com/tj-miller-dev/stock_simulator.git"
}
