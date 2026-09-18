mock_provider "awscc" {
  mock_resource "awscc_mediapackagev2_channel" {
    defaults = {
      arn = "arn:aws:mediapackagev2:us-east-1:123456789012:channelGroup/live-demo/channel/live-demo"
    }
  }
}

variables {
  name               = "live-demo"
  medialive_role_arn = "arn:aws:iam::123456789012:role/live-demo-medialive"
}

run "channel_accepts_hls" {
  command = plan

  assert {
    condition     = awscc_mediapackagev2_channel.this.input_type == "HLS"
    error_message = "Channel must use HLS ingest."
  }

  assert {
    condition     = awscc_mediapackagev2_origin_endpoint.hls.container_type == "TS"
    error_message = "HLS ingest uses TS segments, so the endpoint container must be TS."
  }
}

run "policy_allows_only_the_medialive_role" {
  command = apply

  assert {
    condition     = jsondecode(awscc_mediapackagev2_channel_policy.ingest.policy).Statement[0].Principal.AWS == "arn:aws:iam::123456789012:role/live-demo-medialive"
    error_message = "Only the MediaLive role may ingest."
  }

  assert {
    condition     = jsondecode(awscc_mediapackagev2_channel_policy.ingest.policy).Statement[0].Action == "mediapackagev2:PutObject"
    error_message = "Ingest policy must grant mediapackagev2:PutObject."
  }

  assert {
    condition     = jsondecode(awscc_mediapackagev2_channel_policy.ingest.policy).Statement[0].Resource == "arn:aws:mediapackagev2:us-east-1:123456789012:channelGroup/live-demo/channel/live-demo"
    error_message = "Policy must be scoped to the channel."
  }
}
