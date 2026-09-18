variable "region" {
  description = "AWS region for the state bucket."
  type        = string
  default     = "us-east-1"
}

variable "project" {
  description = "Project name used as a prefix and tag."
  type        = string
  default     = "live-sports-aws"
}
