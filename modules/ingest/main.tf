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
      key_type   = "srt-password"
      role_arn   = aws_iam_role.flow.arn
      secret_arn = aws_secretsmanager_secret.srt.arn
    }
  }

  source_failover_config = var.backup_source_enabled ? {
    state         = "ENABLED"
    failover_mode = "FAILOVER"
    source_priority = {
      primary_source = "${var.name}-srt"
    }
  } : null

  source_monitoring_config = {
    thumbnail_state = var.thumbnails_enabled ? "ENABLED" : "DISABLED"
  }

  depends_on = [
    aws_iam_role_policy.read_secret,
    aws_secretsmanager_secret_version.srt,
  ]
}

# The backup is a second SRT listener on the same flow (same ingest IP, its own port). MediaConnect switches to it
# when the primary stops delivering and back when the primary returns. MERGE (SMPTE 2022-7) needs RTP or RIST, so
# SRT can only use FAILOVER.
resource "awscc_mediaconnect_flow_source" "backup" {
  count = var.backup_source_enabled ? 1 : 0

  flow_arn       = awscc_mediaconnect_flow.this.flow_arn
  name           = "${var.name}-srt-backup"
  description    = "Backup SRT listener for source failover."
  protocol       = "srt-listener"
  ingest_port    = var.backup_ingest_port
  whitelist_cidr = var.whitelist_cidr
  min_latency    = var.min_latency_ms

  decryption = {
    key_type   = "srt-password"
    role_arn   = aws_iam_role.flow.arn
    secret_arn = aws_secretsmanager_secret.srt.arn
  }
}
