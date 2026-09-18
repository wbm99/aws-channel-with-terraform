output "role_arn" {
  description = "ARN of the IAM role MediaLive assumes."
  value       = aws_iam_role.this.arn
}
