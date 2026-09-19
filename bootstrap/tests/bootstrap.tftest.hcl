variables {
  alert_email = "me@example.com"
}

mock_provider "aws" {}

run "state_bucket_is_hardened" {
  command = plan

  assert {
    condition     = startswith(aws_s3_bucket.state.bucket, "live-sports-aws-tfstate-")
    error_message = "State bucket name must start with the project prefix."
  }

  assert {
    condition     = aws_s3_bucket_versioning.state.versioning_configuration[0].status == "Enabled"
    error_message = "State bucket versioning must be enabled."
  }

  assert {
    condition     = aws_s3_bucket_public_access_block.state.block_public_acls && aws_s3_bucket_public_access_block.state.restrict_public_buckets
    error_message = "State bucket must block public access."
  }
}

run "budget_alerts_are_persistent" {
  command = plan

  assert {
    condition     = module.guardrails.budget_name == "live-sports-aws-monthly"
    error_message = "Bootstrap must create the monthly budget."
  }
}
