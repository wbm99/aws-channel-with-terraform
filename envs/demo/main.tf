provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project = var.project
      Env     = "demo"
    }
  }
}

provider "awscc" {
  region = var.region
}

module "guardrails" {
  source = "../../modules/guardrails"

  project     = var.project
  limit_usd   = var.budget_limit_usd
  alert_email = var.alert_email
}

module "ingest" {
  source = "../../modules/ingest"

  name           = "${var.project}-demo"
  whitelist_cidr = var.source_cidr
}
