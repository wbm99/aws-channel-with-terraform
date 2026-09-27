# EventBridge -> CloudWatch Logs gives the console one timeline of state changes, alerts and source health,
# including TR 101 290 flags, for a few cents a month.
resource "aws_cloudwatch_log_group" "events" {
  name              = "/aws/events/${var.name}"
  retention_in_days = var.retention_days
}

resource "aws_cloudwatch_event_rule" "media" {
  name        = "${var.name}-media-events"
  description = "MediaLive and MediaConnect state changes, alerts and health events for the livectl console."

  event_pattern = jsonencode({
    source = ["aws.medialive", "aws.mediaconnect"]
  })
}

resource "aws_cloudwatch_event_target" "logs" {
  rule = aws_cloudwatch_event_rule.media.name
  arn  = aws_cloudwatch_log_group.events.arn
}

# Without this, the rule matches, reports no error, and delivers nothing.
resource "aws_cloudwatch_log_resource_policy" "events" {
  policy_name = "${var.name}-events-to-logs"
  policy_document = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = ["events.amazonaws.com", "delivery.logs.amazonaws.com"] }
      Action    = ["logs:CreateLogStream", "logs:PutLogEvents"]
      Resource  = "${aws_cloudwatch_log_group.events.arn}:*"
    }]
  })
}
