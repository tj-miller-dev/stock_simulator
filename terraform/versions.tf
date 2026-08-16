terraform {
  required_version = ">= 1.9.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.35"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 3.2"
    }
  }

  # State is local (terraform.tfstate in this directory) until you configure
  # a remote backend. Fine solo, but move to S3 (with native S3 locking, no
  # DynamoDB table needed on recent Terraform) before more than one person
  # or machine runs apply against this.
  # backend "s3" {
  #   bucket       = "CHANGE-ME-tfstate"
  #   key          = "stock-simulator/eks.tfstate"
  #   region       = "us-east-1"
  #   use_lockfile = true
  # }
}
