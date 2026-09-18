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

module "medialive_role" {
  source = "../../modules/medialive-role"

  name = "${var.project}-demo"
}

module "package" {
  source = "../../modules/package"

  name               = "${var.project}-demo"
  medialive_role_arn = module.medialive_role.role_arn
}

module "encode" {
  source = "../../modules/encode"

  name       = "${var.project}-demo"
  flow_arn   = module.ingest.flow_arn
  role_arn   = module.medialive_role.role_arn
  ingest_url = module.package.ingest_url
}
