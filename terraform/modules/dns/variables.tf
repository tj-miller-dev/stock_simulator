variable "cluster_name" {
  description = "Name of the EKS cluster to install ExternalDNS into"
  type        = string
}

variable "domain_name" {
  description = "Apex domain (already registered/hosted in Route53) to issue a cert for and manage records under"
  type        = string
}

variable "chart_version" {
  description = "Version of the external-dns Helm chart. Check https://artifacthub.io/packages/helm/external-dns/external-dns for newer releases."
  type        = string
  default     = "1.21.1"
}
