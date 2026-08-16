module "networking" {
  source = "./modules/networking"

  cluster_name = var.cluster_name
  vpc_cidr     = var.vpc_cidr
}

module "cluster" {
  source = "./modules/cluster"

  cluster_name       = var.cluster_name
  kubernetes_version = var.kubernetes_version

  subnet_ids = module.networking.private_subnet_ids

  node_instance_types = var.node_instance_types
  node_min_size       = var.node_min_size
  node_max_size       = var.node_max_size
  node_desired_size   = var.node_desired_size

  depends_on = [module.networking]
}

module "loadbalancer" {
  source = "./modules/loadbalancer"

  cluster_name = var.cluster_name
  aws_region   = var.aws_region
  vpc_id       = module.networking.vpc_id

  depends_on = [module.cluster]
}

module "registry" {
  source = "./modules/registry"

  repository_names = ["stock-simulator-api", "stock-simulator-frontend"]
}

module "gitops" {
  source = "./modules/gitops"

  namespace     = var.argocd_namespace
  chart_version = var.argocd_chart_version

  depends_on = [module.cluster]
}

module "cicd" {
  source = "./modules/cicd"

  cluster_name                = var.cluster_name
  github_repository           = var.github_repository
  github_repository_immutable = var.github_repository_immutable
  deploy_branch               = var.deploy_branch
  ecr_repository_arns         = module.registry.repository_arns
}
