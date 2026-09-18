variable "name" {
  description = "Base name for CloudFront resources."
  type        = string
}

variable "origin_domain" {
  description = "MediaPackage v2 egress domain used as the live origin."
  type        = string
}

variable "cdn_identifier" {
  description = "Value sent in the X-MediaPackageV2-CDNIdentifier header."
  type        = string
  sensitive   = true
}

variable "player_bucket_id" {
  description = "ID of the private S3 bucket that holds the player."
  type        = string
}

variable "player_bucket_arn" {
  description = "ARN of the player bucket."
  type        = string
}

variable "player_bucket_regional_domain_name" {
  description = "Regional domain name of the player bucket."
  type        = string
}
