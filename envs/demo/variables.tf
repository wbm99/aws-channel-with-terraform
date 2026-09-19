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
