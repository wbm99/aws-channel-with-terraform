variable "region" {
  description = "AWS region."
  type        = string
  default     = "us-east-1"
}

variable "project" {
  description = "Project name used for naming and tags."
  type        = string
  default     = "live-sports-aws"
}

variable "source_cidr" {
  description = "CIDR of the machine that pushes the SRT stream, for example 203.0.113.10/32."
  type        = string
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
