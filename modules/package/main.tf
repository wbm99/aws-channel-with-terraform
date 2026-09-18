resource "awscc_mediapackagev2_channel_group" "this" {
  channel_group_name = var.name
}

resource "awscc_mediapackagev2_channel" "this" {
  channel_group_name = awscc_mediapackagev2_channel_group.this.channel_group_name
  channel_name       = var.name
  input_type         = "HLS"
}

resource "awscc_mediapackagev2_channel_policy" "ingest" {
  channel_group_name = awscc_mediapackagev2_channel_group.this.channel_group_name
  channel_name       = awscc_mediapackagev2_channel.this.channel_name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AllowMediaLiveIngest"
        Effect    = "Allow"
        Principal = { AWS = var.medialive_role_arn }
        Action    = "mediapackagev2:PutObject"
        Resource  = awscc_mediapackagev2_channel.this.arn
      }
    ]
  })
}

resource "awscc_mediapackagev2_origin_endpoint" "hls" {
  channel_group_name   = awscc_mediapackagev2_channel_group.this.channel_group_name
  channel_name         = awscc_mediapackagev2_channel.this.channel_name
  origin_endpoint_name = "${var.name}-hls"
  container_type       = "TS"

  hls_manifests = [
    {
      manifest_name = "index"
    }
  ]
}

# Spike only: public read access so playback can be tested without a CDN.
# Plan 3 replaces this with access restricted to CloudFront.
resource "awscc_mediapackagev2_origin_endpoint_policy" "playback" {
  channel_group_name   = awscc_mediapackagev2_channel_group.this.channel_group_name
  channel_name         = awscc_mediapackagev2_channel.this.channel_name
  origin_endpoint_name = awscc_mediapackagev2_origin_endpoint.hls.origin_endpoint_name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AllowPlayback"
        Effect    = "Allow"
        Principal = "*"
        Action    = "mediapackagev2:GetObject"
        Resource  = awscc_mediapackagev2_origin_endpoint.hls.arn
      }
    ]
  })
}
