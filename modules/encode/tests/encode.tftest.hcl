mock_provider "aws" {}

variables {
  name       = "live-demo"
  flow_arn   = "arn:aws:mediaconnect:us-east-1:123456789012:flow:1-abc:live-demo"
  role_arn   = "arn:aws:iam::123456789012:role/live-demo-medialive"
  ingest_url = "https://example.ingest.mediapackagev2.us-east-1.amazonaws.com/in/v1/live-demo/1/live-demo/index.m3u8"
}

run "defaults_to_single_pipeline_and_not_started" {
  command = plan

  assert {
    condition     = aws_medialive_channel.this.channel_class == "SINGLE_PIPELINE"
    error_message = "Default channel class must be SINGLE_PIPELINE."
  }

  assert {
    condition     = aws_medialive_channel.this.start_channel == false
    error_message = "Terraform must not start the channel."
  }
}

run "input_comes_from_the_mediaconnect_flow" {
  command = plan

  assert {
    condition     = aws_medialive_input.this.type == "MEDIACONNECT"
    error_message = "Input must be a MediaConnect input."
  }
}

run "rejects_unknown_channel_class" {
  command = plan

  variables {
    channel_class = "DOUBLE"
  }

  expect_failures = [var.channel_class]
}

run "default_ladder_has_three_renditions" {
  command = plan

  assert {
    condition     = length(aws_medialive_channel.this.encoder_settings[0].video_descriptions) == 3
    error_message = "Default ladder must have 3 video descriptions."
  }

  assert {
    condition     = length(aws_medialive_channel.this.encoder_settings[0].output_groups[0].outputs) == 3
    error_message = "Default ladder must have 3 outputs."
  }
}

run "custom_ladder_is_respected" {
  command = plan

  variables {
    renditions = [
      { name = "720p", width = 1280, height = 720, bitrate = 3000000 },
    ]
  }

  assert {
    condition     = length(aws_medialive_channel.this.encoder_settings[0].video_descriptions) == 1
    error_message = "A one-rung ladder must produce one video description."
  }
}

run "rejects_empty_ladder" {
  command = plan

  variables {
    renditions = []
  }

  expect_failures = [var.renditions]
}

run "rejects_duplicate_rendition_names" {
  command = plan

  variables {
    renditions = [
      { name = "720p", width = 1280, height = 720, bitrate = 3000000 },
      { name = "720p", width = 1280, height = 720, bitrate = 2000000 },
    ]
  }

  expect_failures = [var.renditions]
}

run "writes_encoder_logs_at_info_by_default" {
  command = plan

  assert {
    condition     = aws_medialive_channel.this.log_level == "INFO"
    error_message = "Encoder logs default to INFO."
  }
}

run "rejects_an_unknown_log_level" {
  command = plan

  variables {
    log_level = "VERBOSE"
  }

  expect_failures = [var.log_level]
}

run "passes_scte35_through_to_mediapackage" {
  command = plan

  assert {
    condition = alltrue([
      for output in aws_medialive_channel.this.encoder_settings[0].output_groups[0].outputs :
      output.output_settings[0].hls_output_settings[0].hls_settings[0].standard_hls_settings[0].m3u8_settings[0].scte35_behavior == "PASSTHROUGH"
    ])
    error_message = "Every rendition must carry SCTE-35 in its TS segments, or a cued break never leaves MediaLive."
  }

  assert {
    condition     = aws_medialive_channel.this.encoder_settings[0].output_groups[0].output_group_settings[0].hls_group_settings[0].ad_markers == tolist(["ELEMENTAL_SCTE35"])
    error_message = "The HLS group must mark SCTE-35 breaks in the manifests MediaPackage ingests."
  }
}
