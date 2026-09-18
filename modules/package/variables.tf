variable "name" {
  description = "Base name for the channel group, channel and endpoint."
  type        = string
}

variable "medialive_role_arn" {
  description = "ARN of the MediaLive role allowed to ingest into the channel."
  type        = string
}
