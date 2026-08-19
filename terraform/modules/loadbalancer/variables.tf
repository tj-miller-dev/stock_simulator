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

variable "access_logs_retention_days" {
  description = "Days to keep ALB access logs in S3 before lifecycle expiry. Long enough to see month-over-month repeat traffic, short enough that the bucket and the client-IP retention stay bounded."
  type        = number
  default     = 90
}

variable "chart_version" {
  description = "Version of the eks-charts/aws-load-balancer-controller Helm chart. Check https://github.com/aws/eks-charts/blob/master/stable/aws-load-balancer-controller/Chart.yaml for newer releases."
  type        = string
  default     = "3.5.0"
}
