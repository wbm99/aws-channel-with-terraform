variable "project" {
  description = "Project name used in the budget name."
  type        = string
}

variable "limit_usd" {
  description = "Monthly budget limit in USD."
  type        = number

  validation {
    condition     = var.limit_usd > 0
    error_message = "limit_usd must be greater than zero."
  }
}

variable "alert_email" {
  description = "Email address that receives budget alerts."
  type        = string

  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.alert_email))
    error_message = "alert_email must be a valid email address."
  }
}
