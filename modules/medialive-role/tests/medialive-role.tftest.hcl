mock_provider "aws" {}

variables {
  name = "live-demo"
}

run "role_trusts_medialive" {
  command = plan

  assert {
    condition     = jsondecode(aws_iam_role.this.assume_role_policy).Statement[0].Principal.Service == "medialive.amazonaws.com"
    error_message = "Role must be assumable by MediaLive."
  }

  assert {
    condition     = aws_iam_role.this.name == "live-demo-medialive"
    error_message = "Role name must be derived from var.name."
  }
}
