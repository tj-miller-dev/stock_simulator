variable "cluster_name" {
  description = "Used to name the VPC and tag subnets for EKS auto-discovery"
  type        = string
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
}
