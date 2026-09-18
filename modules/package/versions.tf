terraform {
  required_version = ">= 1.11"

  required_providers {
    awscc = {
      source  = "hashicorp/awscc"
      version = "~> 1.0"
    }
  }
}
