mock_provider "aws" {}
mock_provider "random" {}

variables {
  name          = "live-demo"
  manifest_path = "/out/v1/live-demo/live-demo/live-demo-hls/index.m3u8"
}

run "bucket_is_private" {
  command = plan

  assert {
    condition     = aws_s3_bucket_public_access_block.this.block_public_acls && aws_s3_bucket_public_access_block.this.restrict_public_buckets
    error_message = "Player bucket must block public access."
  }
}

run "page_and_config_are_uploaded" {
  command = plan

  assert {
    condition     = aws_s3_object.index.content_type == "text/html"
    error_message = "index.html must be served as text/html."
  }

  assert {
    condition     = jsondecode(aws_s3_object.config.content).manifestPath == "/out/v1/live-demo/live-demo/live-demo-hls/index.m3u8"
    error_message = "config.json must carry the manifest path."
  }
}
