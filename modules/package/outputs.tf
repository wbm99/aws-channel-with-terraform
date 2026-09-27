# try() keeps the outputs evaluable under mocked providers, where computed lists are empty.
output "ingest_url" {
  description = "HLS ingest URL of the MediaPackage v2 channel."
  value       = try(awscc_mediapackagev2_channel.this.ingest_endpoints[0].url, null)
}

output "hls_manifest_url" {
  description = "HLS playback manifest URL of the origin endpoint."
  value       = try(awscc_mediapackagev2_origin_endpoint.hls.hls_manifest_urls[0], null)
}

output "origin_domain" {
  description = "Egress domain that CloudFront uses as the origin."
  value       = awscc_mediapackagev2_channel_group.this.egress_domain
}

output "hls_manifest_path" {
  description = "Path of the HLS master manifest on the origin."
  value       = "/out/v1/${var.name}/${var.name}/${var.name}-hls/index.m3u8"
}

output "cdn_identifier" {
  description = "Value CloudFront must send in the X-MediaPackageV2-CDNIdentifier header."
  value       = random_uuid.cdn_identifier.result
  sensitive   = true
}

output "channel_group_name" {
  description = "Name of the MediaPackage v2 channel group."
  value       = awscc_mediapackagev2_channel_group.this.channel_group_name
}

output "channel_name" {
  description = "Name of the MediaPackage v2 channel."
  value       = awscc_mediapackagev2_channel.this.channel_name
}

output "origin_endpoint_name" {
  description = "Name of the HLS origin endpoint."
  value       = awscc_mediapackagev2_origin_endpoint.hls.origin_endpoint_name
}
