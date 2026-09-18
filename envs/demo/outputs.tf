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
