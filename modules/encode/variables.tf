variable "name" {
  description = "Base name for the MediaLive input and channel."
  type        = string
}

variable "flow_arn" {
  description = "ARN of the MediaConnect flow that feeds the channel."
  type        = string
}

variable "role_arn" {
  description = "ARN of the IAM role MediaLive assumes."
  type        = string
}

variable "ingest_url" {
  description = "HLS ingest URL of the MediaPackage v2 channel."
  type        = string
}

variable "channel_class" {
  description = "MediaLive channel class. Immutable after creation; changing it replaces the channel."
  type        = string
  default     = "SINGLE_PIPELINE"

  validation {
    condition     = contains(["SINGLE_PIPELINE", "STANDARD"], var.channel_class)
    error_message = "channel_class must be SINGLE_PIPELINE or STANDARD."
  }
}
