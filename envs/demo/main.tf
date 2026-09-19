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

module "player" {
  source = "../../modules/player"

  name          = "${var.project}-demo"
  manifest_path = module.package.hls_manifest_path
}

module "delivery" {
  source = "../../modules/delivery"

  name                               = "${var.project}-demo"
  origin_domain                      = module.package.origin_domain
  cdn_identifier                     = module.package.cdn_identifier
  player_bucket_id                   = module.player.bucket_id
  player_bucket_arn                  = module.player.bucket_arn
  player_bucket_regional_domain_name = module.player.bucket_regional_domain_name
}
