output "flow_arn" {
  description = "ARN of the MediaConnect flow."
  value       = module.ingest.flow_arn
}

output "ingest_ip" {
  description = "Public IP of the SRT listener."
  value       = module.ingest.ingest_ip
}

output "ingest_port" {
  description = "Port of the SRT listener."
  value       = module.ingest.ingest_port
}

output "passphrase_secret_arn" {
  description = "Secrets Manager ARN of the SRT passphrase."
  value       = module.ingest.passphrase_secret_arn
}

output "medialive_channel_id" {
  description = "ID of the MediaLive channel."
  value       = module.encode.channel_id
}

output "hls_manifest_url" {
  description = "HLS playback manifest URL from MediaPackage v2."
  value       = module.package.hls_manifest_url
}

output "player_url" {
  description = "URL of the player page."
  value       = "https://${module.delivery.distribution_domain_name}/"
}

output "medialive_input_id" {
  description = "ID of the MediaLive input."
  value       = module.encode.input_id
}

output "medialive_channel_arn" {
  description = "ARN of the MediaLive channel."
  value       = module.encode.channel_arn
}

output "mediapackage_channel_group" {
  description = "Name of the MediaPackage v2 channel group."
  value       = module.package.channel_group_name
}

output "mediapackage_channel" {
  description = "Name of the MediaPackage v2 channel."
  value       = module.package.channel_name
}

output "mediapackage_endpoint" {
  description = "Name of the MediaPackage v2 HLS origin endpoint."
  value       = module.package.origin_endpoint_name
}

output "distribution_id" {
  description = "ID of the CloudFront distribution."
  value       = module.delivery.distribution_id
}

output "cdn_manifest_url" {
  description = "HLS master manifest URL through CloudFront, as viewers fetch it."
  value       = "https://${module.delivery.distribution_domain_name}${module.package.hls_manifest_path}"
}
