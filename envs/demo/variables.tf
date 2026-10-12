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

variable "source_failover" {
  description = "Add a backup SRT listener to the MediaConnect flow and fail over to it when the primary stops. Adds one MediaConnect input hour (about 0.09 USD) per running hour."
  type        = bool
  default     = true
}

variable "ads" {
  description = "Ad break settings written by the console to console.auto.tfvars.json. The clip path is relative to this directory; modules/player validates it."
  type = object({
    clip          = optional(string)
    clip_seconds  = optional(number)
    duration_mode = optional(string, "per_trigger")
  })
  default = {}
}
