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
