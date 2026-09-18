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

variable "renditions" {
  description = "ABR ladder. Each rendition becomes one video description and one HLS output."
  type = list(object({
    name    = string
    width   = number
    height  = number
    bitrate = number
  }))
  default = [
    { name = "1080p", width = 1920, height = 1080, bitrate = 5000000 },
    { name = "720p", width = 1280, height = 720, bitrate = 3000000 },
    { name = "480p", width = 854, height = 480, bitrate = 1500000 },
  ]

  validation {
    condition     = length(var.renditions) > 0
    error_message = "renditions must contain at least one rendition."
  }

  validation {
    condition     = length(distinct([for r in var.renditions : r.name])) == length(var.renditions)
    error_message = "Rendition names must be unique."
  }
}
