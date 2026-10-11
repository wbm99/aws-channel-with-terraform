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

run "no_ad_clip_by_default" {
  command = plan

  assert {
    condition     = length(aws_s3_object.ad_clip) == 0
    error_message = "Without a clip, nothing is uploaded under ads/."
  }

  assert {
    condition     = jsondecode(aws_s3_object.config.content).adClip == null
    error_message = "Without a clip, config.json must say so with adClip = null."
  }

  assert {
    condition     = output.ads.clip_md5 == null && output.ads.duration_mode == "per_trigger"
    error_message = "The default ad settings are no clip and a duration chosen per trigger."
  }
}

run "ad_clip_is_uploaded_and_announced" {
  command = plan

  variables {
    ads = { clip = "tests/fixtures/clip.mp4", clip_seconds = 24.5, duration_mode = "clip" }
  }

  assert {
    condition     = length(aws_s3_object.ad_clip) == 1 && aws_s3_object.ad_clip[0].key == "ads/clip.mp4"
    error_message = "The clip must be uploaded as ads/clip.mp4."
  }

  assert {
    condition     = aws_s3_object.ad_clip[0].content_type == "video/mp4"
    error_message = "The clip must be served as video/mp4."
  }

  assert {
    condition     = jsondecode(aws_s3_object.config.content).adClip == "/ads/clip.mp4"
    error_message = "config.json must point the player at the clip."
  }

  assert {
    condition     = output.ads.clip_md5 == filemd5("tests/fixtures/clip.mp4") && output.ads.clip_seconds == 24.5
    error_message = "The ads output must carry the clip's MD5 and length, so the console can tell what is deployed."
  }
}

run "ads_script_is_uploaded" {
  command = plan

  assert {
    condition     = aws_s3_object.ads_js.key == "ads.js" && aws_s3_object.ads_js.content_type == "text/javascript"
    error_message = "ads.js must be uploaded next to index.html as JavaScript."
  }
}

run "rejects_an_unknown_duration_mode" {
  command = plan

  variables {
    ads = { duration_mode = "sometimes" }
  }

  expect_failures = [var.ads]
}

run "rejects_a_clip_that_is_not_mp4" {
  command = plan

  variables {
    ads = { clip = "tests/fixtures/clip.mov", clip_seconds = 10 }
  }

  expect_failures = [var.ads]
}

run "rejects_a_clip_that_does_not_exist" {
  command = plan

  variables {
    ads = { clip = "tests/fixtures/missing.mp4", clip_seconds = 10 }
  }

  expect_failures = [var.ads]
}

run "rejects_a_clip_without_its_length" {
  command = plan

  variables {
    ads = { clip = "tests/fixtures/clip.mp4" }
  }

  expect_failures = [var.ads]
}

run "rejects_clip_mode_without_a_clip" {
  command = plan

  variables {
    ads = { duration_mode = "clip" }
  }

  expect_failures = [var.ads]
}

run "rejects_clip_mode_with_a_clip_too_short_for_a_break" {
  command = plan

  variables {
    ads = { clip = "tests/fixtures/clip.mp4", clip_seconds = 2, duration_mode = "clip" }
  }

  expect_failures = [var.ads]
}

run "accepts_a_short_clip_per_trigger" {
  command = plan

  variables {
    ads = { clip = "tests/fixtures/clip.mp4", clip_seconds = 2, duration_mode = "per_trigger" }
  }

  assert {
    condition     = length(aws_s3_object.ad_clip) == 1
    error_message = "A clip of any length can be used when breaks are chosen per trigger; the player loops or cuts it."
  }
}
