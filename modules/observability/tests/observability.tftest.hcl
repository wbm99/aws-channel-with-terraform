mock_provider "aws" {
  mock_resource "aws_cloudwatch_log_group" {
    defaults = {
      arn = "arn:aws:logs:us-east-1:123456789012:log-group:/aws/events/live-demo"
    }
  }
}

variables {
  name = "live-demo"
}

run "media_events_reach_the_log_group" {
  command = apply

  assert {
    condition     = jsondecode(aws_cloudwatch_event_rule.media.event_pattern).source == ["aws.medialive", "aws.mediaconnect"]
    error_message = "The rule must match MediaLive and MediaConnect events."
  }

  assert {
    condition     = aws_cloudwatch_event_target.logs.arn == aws_cloudwatch_log_group.events.arn
    error_message = "The rule must deliver to the events log group."
  }

  assert {
    condition     = aws_cloudwatch_log_group.events.name == "/aws/events/live-demo"
    error_message = "The log group is named /aws/events/<name>."
  }

  assert {
    condition     = aws_cloudwatch_log_group.events.retention_in_days == 7
    error_message = "Default retention is seven days."
  }
}

run "eventbridge_may_write_to_the_group" {
  command = apply

  assert {
    condition = contains(
      jsondecode(aws_cloudwatch_log_resource_policy.events.policy_document).Statement[0].Principal.Service,
      "events.amazonaws.com",
    )
    error_message = "Without a resource policy for events.amazonaws.com the rule silently delivers nothing."
  }

  assert {
    condition     = jsondecode(aws_cloudwatch_log_resource_policy.events.policy_document).Statement[0].Resource == "${aws_cloudwatch_log_group.events.arn}:*"
    error_message = "The policy must cover the streams of this log group only."
  }
}

run "rejects_an_unsupported_retention" {
  command = plan

  variables {
    retention_days = 8
  }

  expect_failures = [var.retention_days]
}
