output "channel_id" {
  description = "ID of the MediaLive channel."
  value       = aws_medialive_channel.this.channel_id
}

output "channel_arn" {
  description = "ARN of the MediaLive channel."
  value       = aws_medialive_channel.this.arn
}
