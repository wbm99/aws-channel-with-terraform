output "log_group_name" {
  description = "Name of the log group that receives MediaLive and MediaConnect events."
  value       = aws_cloudwatch_log_group.events.name
}

output "rule_name" {
  description = "Name of the EventBridge rule."
  value       = aws_cloudwatch_event_rule.media.name
}
