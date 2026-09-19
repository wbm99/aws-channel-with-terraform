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

variable "alert_email" {
  description = "Email address that receives budget alerts."
  type        = string
}

variable "budget_limit_usd" {
  description = "Monthly budget limit in USD."
  type        = number
  default     = 25
}
