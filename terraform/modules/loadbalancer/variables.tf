variable "cluster_name" {
  description = "Name of the EKS cluster to install the controller into"
  type        = string
}

variable "aws_region" {
  description = "AWS region the cluster runs in"
  type        = string
}

variable "vpc_id" {
  description = "VPC the cluster's subnets live in"
  type        = string
}

variable "chart_version" {
  description = "Version of the eks-charts/aws-load-balancer-controller Helm chart. Check https://github.com/aws/eks-charts/blob/master/stable/aws-load-balancer-controller/Chart.yaml for newer releases."
  type        = string
  default     = "3.5.0"
}
