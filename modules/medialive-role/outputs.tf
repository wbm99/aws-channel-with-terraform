output "role_arn" {
  description = "ARN of the IAM role MediaLive assumes. Consumers wait for the runtime policy, so they never use the role before it has its permissions."
  value       = aws_iam_role.this.arn

  depends_on = [aws_iam_role_policy.this]
}
