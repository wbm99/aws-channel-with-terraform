output "bucket_id" {
  description = "ID of the player bucket."
  value       = aws_s3_bucket.this.id
}

output "bucket_arn" {
  description = "ARN of the player bucket."
  value       = aws_s3_bucket.this.arn
}

output "bucket_regional_domain_name" {
  description = "Regional domain name of the player bucket, used as the CloudFront S3 origin."
  value       = aws_s3_bucket.this.bucket_regional_domain_name
}

output "ads" {
  description = "The deployed ad settings: the clip's MD5 (null without a clip), its length and the duration mode. The console compares them with what it saved."
  value = {
    clip_md5      = var.ads.clip == null ? null : filemd5(var.ads.clip)
    clip_seconds  = var.ads.clip_seconds
    duration_mode = var.ads.duration_mode
  }
}
