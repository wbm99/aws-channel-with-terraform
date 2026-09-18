# try() keeps the outputs evaluable under mocked providers, where computed lists are empty.
output "ingest_url" {
  description = "HLS ingest URL of the MediaPackage v2 channel."
  value       = try(awscc_mediapackagev2_channel.this.ingest_endpoints[0].url, null)
}

output "hls_manifest_url" {
  description = "HLS playback manifest URL of the origin endpoint."
  value       = try(awscc_mediapackagev2_origin_endpoint.hls.hls_manifest_urls[0], null)
}

output "egress_domain" {
  description = "Egress domain of the channel group."
  value       = awscc_mediapackagev2_channel_group.this.egress_domain
}
