mock_provider "aws" {
  mock_data "aws_iam_policy_document" {
    defaults = {
      json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}"
    }
  }
}
mock_provider "awscc" {}
mock_provider "random" {}

variables {
  name           = "live-demo"
  whitelist_cidr = "203.0.113.10/32"
}

run "flow_is_srt_listener" {
  command = plan

  assert {
    condition     = awscc_mediaconnect_flow.this.source.protocol == "srt-listener"
    error_message = "Flow source must use the srt-listener protocol."
  }

  assert {
    condition     = awscc_mediaconnect_flow.this.source.whitelist_cidr == "203.0.113.10/32"
    error_message = "Flow must whitelist the configured CIDR."
  }

  assert {
    condition     = awscc_mediaconnect_flow.this.source.decryption.key_type == "srt-password"
    error_message = "Flow must use SRT passphrase decryption."
  }
}

run "rejects_open_cidr" {
  command = plan

  variables {
    whitelist_cidr = "0.0.0.0/0"
  }

  expect_failures = [var.whitelist_cidr]
}

run "rejects_invalid_cidr" {
  command = plan

  variables {
    whitelist_cidr = "not-a-cidr"
  }

  expect_failures = [var.whitelist_cidr]
}
