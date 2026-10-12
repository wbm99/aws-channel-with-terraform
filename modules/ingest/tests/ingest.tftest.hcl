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

run "thumbnails_enabled_by_default" {
  command = plan

  assert {
    condition     = awscc_mediaconnect_flow.this.source_monitoring_config.thumbnail_state == "ENABLED"
    error_message = "Thumbnails must be enabled by default."
  }
}

run "thumbnails_can_be_disabled" {
  command = plan

  variables {
    thumbnails_enabled = false
  }

  assert {
    condition     = awscc_mediaconnect_flow.this.source_monitoring_config.thumbnail_state == "DISABLED"
    error_message = "thumbnails_enabled = false must disable thumbnails."
  }
}

run "no_backup_source_by_default" {
  command = plan

  assert {
    condition     = length(awscc_mediaconnect_flow_source.backup) == 0
    error_message = "The backup source must be off by default."
  }

  assert {
    condition     = output.backup_ingest_port == null
    error_message = "backup_ingest_port must be null without a backup source."
  }
}

run "backup_source_enables_failover" {
  command = plan

  variables {
    backup_source_enabled = true
  }

  assert {
    condition     = awscc_mediaconnect_flow.this.source_failover_config.state == "ENABLED"
    error_message = "A backup source must enable source failover on the flow."
  }

  assert {
    condition     = awscc_mediaconnect_flow.this.source_failover_config.failover_mode == "FAILOVER"
    error_message = "SRT sources can only use FAILOVER mode; MERGE needs RTP or RIST."
  }

  assert {
    condition     = awscc_mediaconnect_flow.this.source_failover_config.source_priority.primary_source == awscc_mediaconnect_flow.this.source.name
    error_message = "The original SRT listener must be the primary source."
  }

  assert {
    condition     = awscc_mediaconnect_flow_source.backup[0].protocol == "srt-listener" && awscc_mediaconnect_flow_source.backup[0].ingest_port == 5001
    error_message = "The backup must be an SRT listener on port 5001."
  }

  assert {
    condition     = awscc_mediaconnect_flow_source.backup[0].whitelist_cidr == "203.0.113.10/32" && awscc_mediaconnect_flow_source.backup[0].decryption.key_type == "srt-password"
    error_message = "The backup must use the primary's CIDR and SRT passphrase decryption."
  }
}

run "rejects_backup_port_equal_to_primary" {
  command = plan

  variables {
    backup_source_enabled = true
    backup_ingest_port    = 5000
  }

  expect_failures = [var.backup_ingest_port]
}
