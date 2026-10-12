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

variable "thumbnails_enabled" {
  description = "Generate source thumbnails (JPEG, 480x270) while the flow is active, viewable in the MediaConnect console."
  type        = bool
  default     = true
}

variable "backup_source_enabled" {
  description = "Add a second SRT listener and enable source failover on the flow. Each source is billed as a MediaConnect input hour while the flow runs."
  type        = bool
  default     = false
}

variable "backup_ingest_port" {
  description = "UDP port the backup SRT listener accepts content on. Must differ from ingest_port, since both sources share the flow's ingest IP."
  type        = number
  default     = 5001

  validation {
    condition     = var.backup_ingest_port != var.ingest_port
    error_message = "backup_ingest_port must differ from ingest_port."
  }
}
