output "flow_arn" {
  description = "ARN of the MediaConnect flow."
  value       = awscc_mediaconnect_flow.this.flow_arn
}

output "ingest_ip" {
  description = "Public IP address the SRT listener accepts content on."
  value       = awscc_mediaconnect_flow.this.source.ingest_ip
}

output "ingest_port" {
  description = "Port the SRT listener accepts content on."
  value       = var.ingest_port
}

output "passphrase_secret_arn" {
  description = "ARN of the Secrets Manager secret holding the SRT passphrase."
  value       = aws_secretsmanager_secret.srt.arn
}

output "backup_ingest_port" {
  description = "Port the backup SRT listener accepts content on, or null when backup_source_enabled is false."
  value       = var.backup_source_enabled ? var.backup_ingest_port : null
}
