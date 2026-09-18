resource "aws_medialive_input" "this" {
  name     = "${var.name}-input"
  type     = "MEDIACONNECT"
  role_arn = var.role_arn

  media_connect_flows {
    flow_arn = var.flow_arn
  }
}

resource "aws_medialive_channel" "this" {
  name          = var.name
  channel_class = var.channel_class
  role_arn      = var.role_arn
  start_channel = false

  input_specification {
    codec            = "AVC"
    input_resolution = "HD"
    maximum_bitrate  = "MAX_10_MBPS"
  }

  input_attachments {
    input_attachment_name = "mediaconnect-srt"
    input_id              = aws_medialive_input.this.id
  }

  destinations {
    id = "mediapackage-v2"

    settings {
      url = var.ingest_url
    }
  }

  encoder_settings {
    timecode_config {
      source = "EMBEDDED"
    }

    dynamic "video_descriptions" {
      for_each = var.renditions

      content {
        name   = "video_${video_descriptions.value.name}"
        width  = video_descriptions.value.width
        height = video_descriptions.value.height

        codec_settings {
          h264_settings {
            bitrate               = video_descriptions.value.bitrate
            rate_control_mode     = "CBR"
            framerate_control     = "SPECIFIED"
            framerate_numerator   = 30
            framerate_denominator = 1
            gop_size              = 2
            gop_size_units        = "SECONDS"
            scene_change_detect   = "DISABLED"
          }
        }
      }
    }

    audio_descriptions {
      name                = "audio_main"
      audio_selector_name = "default"

      codec_settings {
        aac_settings {
          bitrate     = 128000
          coding_mode = "CODING_MODE_2_0"
          sample_rate = 48000
        }
      }
    }

    output_groups {
      name = "hls-to-mediapackage"

      output_group_settings {
        hls_group_settings {
          segment_length = 6

          destination {
            destination_ref_id = "mediapackage-v2"
          }

          hls_cdn_settings {
            hls_basic_put_settings {
              connection_retry_interval = 1
              filecache_duration        = 300
              num_retries               = 10
              restart_delay             = 15
            }
          }
        }
      }

      dynamic "outputs" {
        for_each = var.renditions

        content {
          output_name             = outputs.value.name
          video_description_name  = "video_${outputs.value.name}"
          audio_description_names = ["audio_main"]

          output_settings {
            hls_output_settings {
              name_modifier = "_${outputs.value.name}"

              hls_settings {
                standard_hls_settings {
                  m3u8_settings {}
                }
              }
            }
          }
        }
      }
    }
  }
}
