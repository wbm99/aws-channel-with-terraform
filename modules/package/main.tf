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

  # Keep splice inserts as ad markers; without a filter MediaPackage drops SCTE-35.
  segment = {
    scte = {
      scte_filter = ["SPLICE_INSERT"]
    }
  }

  hls_manifests = [
    {
      manifest_name = "index"
      # Breaks appear as EXT-X-DATERANGE. HLS requires EXT-X-PROGRAM-DATE-TIME beside them, and MediaPackage writes
      # it only when an interval is set; the player uses it to place a break on its timeline.
      scte_hls = {
        ad_marker_hls = "DATERANGE"
      }
      program_date_time_interval_seconds = 6
    }
  ]
}

resource "random_uuid" "cdn_identifier" {}

resource "aws_secretsmanager_secret" "cdn" {
  name                    = "MediaPackageV2/${var.name}-cdn-auth"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "cdn" {
  secret_id = aws_secretsmanager_secret.cdn.id

  secret_string = jsonencode({
    MediaPackageV2CDNIdentifier = random_uuid.cdn_identifier.result
  })
}

resource "aws_iam_role" "cdn_auth" {
  name = "${var.name}-mediapackage-cdn-auth"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Action    = "sts:AssumeRole"
        Principal = { Service = "mediapackagev2.amazonaws.com" }
      }
    ]
  })
}

resource "aws_iam_role_policy" "cdn_auth" {
  name = "read-cdn-secret"
  role = aws_iam_role.cdn_auth.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
        Resource = aws_secretsmanager_secret.cdn.arn
      },
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:BatchGetSecretValue"]
        Resource = "*"
      },
    ]
  })
}

resource "awscc_mediapackagev2_origin_endpoint_policy" "playback" {
  channel_group_name   = awscc_mediapackagev2_channel_group.this.channel_group_name
  channel_name         = awscc_mediapackagev2_channel.this.channel_name
  origin_endpoint_name = awscc_mediapackagev2_origin_endpoint.hls.origin_endpoint_name

  cdn_auth_configuration = {
    cdn_identifier_secret_arns = [aws_secretsmanager_secret.cdn.arn]
    secrets_role_arn           = aws_iam_role.cdn_auth.arn
  }

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AllowGetObjectForAuthorizedRequests"
        Effect    = "Allow"
        Principal = "*"
        Action    = "mediapackagev2:GetObject"
        Resource  = awscc_mediapackagev2_origin_endpoint.hls.arn

        Condition = {
          Bool = {
            "mediapackagev2:RequestHasMatchingCdnAuthHeader" = "true"
          }
        }
      }
    ]
  })

  depends_on = [
    aws_iam_role_policy.cdn_auth,
    aws_secretsmanager_secret_version.cdn,
  ]
}
