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
