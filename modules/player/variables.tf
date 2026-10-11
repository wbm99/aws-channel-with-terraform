variable "name" {
  description = "Base name for the player bucket."
  type        = string
}

variable "manifest_path" {
  description = "Path of the HLS master manifest, relative to the site root (served by the same CloudFront distribution)."
  type        = string
}

variable "ads" {
  description = "Ad break settings from the console: an optional MP4 clip, its length in seconds, and how break durations are set (\"clip\" or \"per_trigger\")."
  type = object({
    clip          = optional(string)
    clip_seconds  = optional(number)
    duration_mode = optional(string, "per_trigger")
  })
  default = {}

  validation {
    condition     = contains(["clip", "per_trigger"], var.ads.duration_mode)
    error_message = "ads.duration_mode must be \"clip\" or \"per_trigger\"."
  }

  # Conditionals rather than ||: Terraform evaluates both sides of ||, and fileexists(null) is an error.
  validation {
    condition     = var.ads.clip == null ? true : (endswith(var.ads.clip, ".mp4") && fileexists(var.ads.clip))
    error_message = "ads.clip must be an existing .mp4 file. Add the clip again in the console's Configuration > Ads tab."
  }

  validation {
    condition     = var.ads.clip == null ? true : (var.ads.clip_seconds == null ? false : var.ads.clip_seconds > 0)
    error_message = "ads.clip_seconds must be the clip's length in seconds, greater than 0."
  }

  validation {
    condition     = var.ads.duration_mode != "clip" || var.ads.clip != null
    error_message = "ads.duration_mode = \"clip\" needs a clip; without one, breaks are chosen per trigger."
  }

  validation {
    condition     = var.ads.duration_mode != "clip" || (var.ads.clip_seconds == null ? false : (var.ads.clip_seconds >= 5 && var.ads.clip_seconds <= 180))
    error_message = "ads.duration_mode = \"clip\" needs a clip of 5 to 180 seconds; other clips are used with \"per_trigger\"."
  }
}
