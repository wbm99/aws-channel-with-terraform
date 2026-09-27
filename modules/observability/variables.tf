variable "name" {
  description = "Base name for the log group, rule and resource policy."
  type        = string
}

variable "retention_days" {
  description = "Days to keep MediaLive and MediaConnect events in the log group."
  type        = number
  default     = 7

  validation {
    condition     = contains([1, 3, 5, 7, 14, 30], var.retention_days)
    error_message = "retention_days must be one of 1, 3, 5, 7, 14 or 30."
  }
}
