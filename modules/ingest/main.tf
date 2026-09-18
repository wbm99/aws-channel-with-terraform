resource "random_password" "srt" {
  length  = 32
  special = false
}

resource "aws_secretsmanager_secret" "srt" {
  name                    = "${var.name}-srt-passphrase"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "srt" {
  secret_id     = aws_secretsmanager_secret.srt.id
  secret_string = random_password.srt.result
}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["mediaconnect.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "read_secret" {
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.srt.arn]
  }
}

resource "aws_iam_role" "flow" {
  name               = "${var.name}-mediaconnect"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

resource "aws_iam_role_policy" "read_secret" {
  name   = "read-srt-passphrase"
  role   = aws_iam_role.flow.id
  policy = data.aws_iam_policy_document.read_secret.json
}

resource "awscc_mediaconnect_flow" "this" {
  name = var.name

  source = {
    name           = "${var.name}-srt"
    protocol       = "srt-listener"
    ingest_port    = var.ingest_port
    whitelist_cidr = var.whitelist_cidr
    min_latency    = var.min_latency_ms

    decryption = {
      algorithm  = "aes256"
      key_type   = "srt-password"
      role_arn   = aws_iam_role.flow.arn
      secret_arn = aws_secretsmanager_secret.srt.arn
    }
  }

  depends_on = [
    aws_iam_role_policy.read_secret,
    aws_secretsmanager_secret_version.srt,
  ]
}
