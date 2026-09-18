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
