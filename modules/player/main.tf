resource "random_id" "suffix" {
  byte_length = 4
}

resource "aws_s3_bucket" "this" {
  bucket        = "${var.name}-player-${random_id.suffix.hex}"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "this" {
  bucket = aws_s3_bucket.this.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_object" "index" {
  bucket       = aws_s3_bucket.this.id
  key          = "index.html"
  source       = "${path.module}/site/index.html"
  etag         = filemd5("${path.module}/site/index.html")
  content_type = "text/html"
}

resource "aws_s3_object" "config" {
  bucket       = aws_s3_bucket.this.id
  key          = "config.json"
  content      = jsonencode({ manifestPath = var.manifest_path, adClip = var.ads.clip == null ? null : "/ads/clip.mp4" })
  content_type = "application/json"
}

# Break scheduling for the player, kept apart from index.html so it can be tested on its own.
resource "aws_s3_object" "ads_js" {
  bucket       = aws_s3_bucket.this.id
  key          = "ads.js"
  source       = "${path.module}/site/ads.js"
  etag         = filemd5("${path.module}/site/ads.js")
  content_type = "text/javascript"
}

resource "aws_s3_object" "ad_clip" {
  count = var.ads.clip == null ? 0 : 1

  bucket       = aws_s3_bucket.this.id
  key          = "ads/clip.mp4"
  source       = var.ads.clip
  etag         = filemd5(var.ads.clip)
  content_type = "video/mp4"
}
