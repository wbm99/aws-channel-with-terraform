mock_provider "aws" {
  mock_resource "aws_secretsmanager_secret" {
    defaults = {
      arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:MediaPackageV2/live-demo-cdn-auth-AbCdEf"
    }
  }

  mock_resource "aws_iam_role" {
    defaults = {
      arn = "arn:aws:iam::123456789012:role/live-demo-mediapackage-cdn-auth"
    }
  }
}
mock_provider "random" {}
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

run "endpoint_requires_cdn_auth_header" {
  command = apply

  assert {
    condition     = jsondecode(awscc_mediapackagev2_origin_endpoint_policy.playback.policy).Statement[0].Condition.Bool["mediapackagev2:RequestHasMatchingCdnAuthHeader"] == "true"
    error_message = "Playback must require the CDN authorization header."
  }

  assert {
    condition     = jsondecode(awscc_mediapackagev2_origin_endpoint_policy.playback.policy).Statement[0].Action == "mediapackagev2:GetObject"
    error_message = "Endpoint policy must grant mediapackagev2:GetObject."
  }

  assert {
    condition     = length(awscc_mediapackagev2_origin_endpoint_policy.playback.cdn_auth_configuration.cdn_identifier_secret_arns) == 1
    error_message = "CDN authorization must reference exactly one secret."
  }
}

run "manifest_path_matches_names" {
  command = plan

  assert {
    condition     = output.hls_manifest_path == "/out/v1/live-demo/live-demo/live-demo-hls/index.m3u8"
    error_message = "Manifest path must be built from the group, channel and endpoint names."
  }
}

run "secret_stores_identifier_under_expected_key" {
  command = apply

  assert {
    condition     = can(jsondecode(aws_secretsmanager_secret_version.cdn.secret_string).MediaPackageV2CDNIdentifier)
    error_message = "Secret must be JSON with key MediaPackageV2CDNIdentifier."
  }
}
