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
