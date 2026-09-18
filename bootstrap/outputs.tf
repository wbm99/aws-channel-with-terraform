output "state_bucket_name" {
  description = "Name of the S3 bucket that holds remote Terraform state."
  value       = aws_s3_bucket.state.bucket
}
