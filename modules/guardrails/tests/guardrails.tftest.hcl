mock_provider "aws" {}

variables {
  project     = "live-sports-aws"
  limit_usd   = 25
  alert_email = "me@example.com"
}

run "budget_has_three_alerts" {
  command = plan

  assert {
    condition     = length(aws_budgets_budget.monthly.notification) == 3
    error_message = "Budget must have 50%, 80% and forecast 100% notifications."
  }

  assert {
    condition     = aws_budgets_budget.monthly.limit_amount == "25"
    error_message = "Budget limit must equal limit_usd."
  }
}

run "rejects_non_positive_limit" {
  command = plan

  variables {
    limit_usd = 0
  }

  expect_failures = [var.limit_usd]
}

run "rejects_bad_email" {
  command = plan

  variables {
    alert_email = "not-an-email"
  }

  expect_failures = [var.alert_email]
}
