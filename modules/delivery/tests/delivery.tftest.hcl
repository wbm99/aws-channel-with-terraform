mock_provider "aws" {
  mock_data "aws_iam_policy_document" {
    defaults = {
      json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}"
    }
  }
}

variables {
  name                               = "live-demo"
  origin_domain                      = "eqk18y.egress.szy3xx.mediapackagev2.us-east-1.amazonaws.com"
  cdn_identifier                     = "9ceebbe7-9607-4552-8764-876e47032660"
  player_bucket_id                   = "live-demo-player-abcd1234"
  player_bucket_arn                  = "arn:aws:s3:::live-demo-player-abcd1234"
  player_bucket_regional_domain_name = "live-demo-player-abcd1234.s3.us-east-1.amazonaws.com"
}

run "two_origins_and_https_only_to_mediapackage" {
  command = apply

  assert {
    condition     = length(aws_cloudfront_distribution.this.origin) == 2
    error_message = "Distribution must have the player and MediaPackage origins."
  }

  assert {
    condition = anytrue([
      for o in aws_cloudfront_distribution.this.origin :
      o.domain_name == "eqk18y.egress.szy3xx.mediapackagev2.us-east-1.amazonaws.com"
      && one(o.custom_origin_config).origin_protocol_policy == "https-only"
    ])
    error_message = "MediaPackage origin must be reached over HTTPS only."
  }
}

run "mediapackage_origin_sends_the_cdn_header" {
  command = apply

  assert {
    condition = anytrue([
      for o in aws_cloudfront_distribution.this.origin :
      anytrue([for h in o.custom_header : h.name == "X-MediaPackageV2-CDNIdentifier"])
    ])
    error_message = "MediaPackage origin must send the X-MediaPackageV2-CDNIdentifier header."
  }
}

run "manifests_and_segments_have_separate_behaviors" {
  command = apply

  assert {
    condition     = length(aws_cloudfront_distribution.this.ordered_cache_behavior) == 2
    error_message = "Expected one behavior for manifests and one for segments."
  }

  assert {
    condition     = aws_cloudfront_cache_policy.manifest.default_ttl < aws_cloudfront_cache_policy.segment.default_ttl
    error_message = "Manifests must be cached for less time than segments."
  }
}

run "viewers_are_redirected_to_https" {
  command = apply

  assert {
    condition     = aws_cloudfront_distribution.this.default_cache_behavior[0].viewer_protocol_policy == "redirect-to-https"
    error_message = "Viewers must be redirected to HTTPS."
  }
}
