terraform {
  required_version = ">= 1.9.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # Local state, as before -- terraform.tfstate in this directory.
  #
  # Note this is a DIFFERENT state file from the EKS stack's, which still lives
  # in ../terraform/ on the `main` branch. That separation is deliberate: the two
  # stacks are torn down and stood up independently, and a shared state file
  # would make "destroy the old one" and "apply the new one" the same operation.
}
