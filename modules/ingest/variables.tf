variable "name" {
  description = "Base name for the flow and related resources."
  type        = string
}

variable "whitelist_cidr" {
  description = "CIDR block allowed to contribute SRT content, for example 203.0.113.10/32."
  type        = string

  validation {
    condition     = can(cidrhost(var.whitelist_cidr, 0)) && var.whitelist_cidr != "0.0.0.0/0"
    error_message = "whitelist_cidr must be a valid CIDR block and must not be 0.0.0.0/0."
  }
}

variable "ingest_port" {
  description = "UDP port the SRT listener accepts content on."
  type        = number
  default     = 5000
}

variable "min_latency_ms" {
  description = "Minimum SRT latency in milliseconds."
  type        = number
  default     = 2000
}
