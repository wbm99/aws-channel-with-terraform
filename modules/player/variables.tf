variable "name" {
  description = "Base name for the player bucket."
  type        = string
}

variable "manifest_path" {
  description = "Path of the HLS master manifest, relative to the site root (served by the same CloudFront distribution)."
  type        = string
}
