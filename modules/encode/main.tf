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

    video_descriptions {
      name   = "video_720p"
      width  = 1280
      height = 720

      codec_settings {
        h264_settings {
          bitrate               = 3000000
          rate_control_mode     = "CBR"
          framerate_control     = "SPECIFIED"
          framerate_numerator   = 30
          framerate_denominator = 1
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
            hls_basic_put_settings {}
          }
        }
      }

      outputs {
        output_name             = "720p"
        video_description_name  = "video_720p"
        audio_description_names = ["audio_main"]

        output_settings {
          hls_output_settings {
            name_modifier = "_720p"

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
