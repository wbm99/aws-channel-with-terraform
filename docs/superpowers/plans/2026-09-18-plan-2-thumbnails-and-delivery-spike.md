# Thumbnails and Delivery-Path Spike Implementation Plan (Plan 2 of 4)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable MediaConnect source thumbnails, then find out empirically whether MediaLive HLS output can feed a MediaPackage v2 channel (`input_type = "HLS"`) declaratively, ending at a hard decision gate.

**Architecture:** `modules/ingest` gains a thumbnail variable. Three new modules build the path under test: `medialive-role` (IAM role for MediaLive), `package` (`awscc` MediaPackage v2 channel group, channel, ingest policy, origin endpoint), `encode` (MediaLive input from the MediaConnect flow, and a channel whose HLS output group PUTs to the v2 ingest URL). `envs/demo` wires them, and a live run shows whether the manifest becomes playable.

**Tech Stack:** Terraform >= 1.11; providers `hashicorp/aws ~> 6.0` (MediaLive, IAM), `hashicorp/awscc ~> 1.0` (MediaConnect, MediaPackage v2); FFmpeg; AWS CLI v2.

**Spec:** `docs/superpowers/specs/2026-09-18-live-srt-pipeline-design.md`

## Roadmap (revised)

- Plan 1: foundation and ingest spike. **Done.**
- **Plan 2 (this plan):** thumbnails + delivery-path spike. Ends at a decision gate.
- Plan 3: build the chosen delivery path fully (ABR ladder, CloudFront, player). Written after the gate.
- Plan 4: Python CLI, FFmpeg script, end-to-end demo, README.

## Global Constraints

- Region: `us-east-1`.
- Terraform `required_version = ">= 1.11"`; remote state in S3 with native locking.
- Budget: $25 monthly. This plan's live run must stay short (single-pipeline MediaLive channel, a few minutes) and end with `terraform destroy` and a clean check.
- `channel_class` is a module variable, default `SINGLE_PIPELINE`; `start_channel = false` (started with the CLI, not by Terraform).
- Everything declarative: no `terraform_data`, `local-exec` or CLI-created resources in this plan. If that proves impossible, STOP at the gate and ask the user.
- Every module has `main.tf`, `variables.tf`, `outputs.tf`, `versions.tf`; providers pinned; follow the `terraform-style-guide` skill; tests use `terraform test` with `mock_provider`, `command = plan`, in `<module>/tests/`.
- Spike simplification: the MediaLive role uses broad `Resource = "*"` on a few actions. Plan 3 tightens it if the path survives.

## File Structure

```
modules/ingest/{variables.tf,main.tf,tests/ingest.tftest.hcl}          (modify)
modules/medialive-role/{versions.tf,variables.tf,main.tf,outputs.tf,tests/medialive-role.tftest.hcl}
modules/package/{versions.tf,variables.tf,main.tf,outputs.tf,tests/package.tftest.hcl}
modules/encode/{versions.tf,variables.tf,main.tf,outputs.tf,tests/encode.tftest.hcl}
envs/demo/{main.tf,outputs.tf}                                          (modify)
```

---

### Task 1: Ingest source thumbnails

**Files:**
- Modify: `modules/ingest/variables.tf`, `modules/ingest/main.tf`
- Test: `modules/ingest/tests/ingest.tftest.hcl`

**Interfaces:**
- Consumes: existing `modules/ingest` variables.
- Produces: variable `thumbnails_enabled` (bool, default `true`).

- [ ] **Step 1: Confirm the schema field and its allowed values**

Run:
```bash
cd /tmp/awscc-schema && terraform providers schema -json | python3 -c "import json,sys; s=json.load(sys.stdin)['provider_schemas']['registry.terraform.io/hashicorp/awscc']['resource_schemas']['awscc_mediaconnect_flow']['block']['attributes']['source_monitoring_config']; print(s['nested_type']['attributes']['thumbnail_state'])"
```
Expected: a `string` attribute. The CloudFormation reference lists `ENABLED | DISABLED`; if the description differs, use what is printed.

- [ ] **Step 2: Write the failing test**

Append to `modules/ingest/tests/ingest.tftest.hcl`:

```hcl
run "thumbnails_enabled_by_default" {
  command = plan

  assert {
    condition     = awscc_mediaconnect_flow.this.source_monitoring_config.thumbnail_state == "ENABLED"
    error_message = "Thumbnails must be enabled by default."
  }
}

run "thumbnails_can_be_disabled" {
  command = plan

  variables {
    thumbnails_enabled = false
  }

  assert {
    condition     = awscc_mediaconnect_flow.this.source_monitoring_config.thumbnail_state == "DISABLED"
    error_message = "thumbnails_enabled = false must disable thumbnails."
  }
}
```

- [ ] **Step 3: Run it to verify it fails**

Run: `cd modules/ingest && terraform init -backend=false -input=false && terraform test`
Expected: the two new runs FAIL (`source_monitoring_config` is null / unsupported), the earlier three still pass.

- [ ] **Step 4: Write the implementation**

Append to `modules/ingest/variables.tf`:

```hcl
variable "thumbnails_enabled" {
  description = "Generate source thumbnails (JPEG, 480x270) while the flow is active, viewable in the MediaConnect console."
  type        = bool
  default     = true
}
```

In `modules/ingest/main.tf`, inside `resource "awscc_mediaconnect_flow" "this"`, add after the `source = { ... }` block:

```hcl
  source_monitoring_config = {
    thumbnail_state = var.thumbnails_enabled ? "ENABLED" : "DISABLED"
  }
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd modules/ingest && terraform fmt -check && terraform validate && terraform test`
Expected: `Success! 5 passed, 0 failed.`

- [ ] **Step 6: Commit**

```bash
git add modules/ingest
git commit -m "feat: enable MediaConnect source thumbnails in ingest module"
```

---

### Task 2: MediaLive role module

**Files:**
- Create: `modules/medialive-role/{versions.tf,variables.tf,main.tf,outputs.tf}`
- Test: `modules/medialive-role/tests/medialive-role.tftest.hcl`

**Interfaces:**
- Consumes: none.
- Produces: variable `name` (string); output `role_arn` (string).

- [ ] **Step 1: Write the failing test**

`modules/medialive-role/tests/medialive-role.tftest.hcl`:

```hcl
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
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd modules/medialive-role && terraform init -backend=false -input=false && terraform test`
Expected: FAIL (no configuration).

- [ ] **Step 3: Write the implementation**

`versions.tf`:

```hcl
terraform {
  required_version = ">= 1.11"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}
```

`variables.tf`:

```hcl
variable "name" {
  description = "Base name for the role."
  type        = string
}
```

`main.tf`:

```hcl
resource "aws_iam_role" "this" {
  name = "${var.name}-medialive"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Action    = "sts:AssumeRole"
        Principal = { Service = "medialive.amazonaws.com" }
      }
    ]
  })
}

resource "aws_iam_role_policy" "this" {
  name = "medialive-runtime"
  role = aws_iam_role.this.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "MediaConnectInput"
        Effect = "Allow"
        Action = [
          "mediaconnect:ManagedDescribeFlow",
          "mediaconnect:ManagedAddOutput",
          "mediaconnect:ManagedRemoveOutput",
          "mediaconnect:DescribeFlow",
        ]
        Resource = "*"
      },
      {
        Sid    = "Logs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ]
        Resource = "*"
      },
    ]
  })
}
```

`outputs.tf`:

```hcl
output "role_arn" {
  description = "ARN of the IAM role MediaLive assumes."
  value       = aws_iam_role.this.arn
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd modules/medialive-role && terraform fmt -check && terraform validate && terraform test`
Expected: `Success! 1 passed, 0 failed.`

- [ ] **Step 5: Commit**

```bash
git add modules/medialive-role
git commit -m "feat: add MediaLive role module"
```

---

### Task 3: Package module (MediaPackage v2 via awscc)

**Files:**
- Create: `modules/package/{versions.tf,variables.tf,main.tf,outputs.tf}`
- Test: `modules/package/tests/package.tftest.hcl`

**Interfaces:**
- Consumes: `medialive_role_arn` (string) from `modules/medialive-role`.
- Produces: variables `name` (string), `medialive_role_arn` (string); outputs `ingest_url` (string), `hls_manifest_url` (string), `egress_domain` (string).

- [ ] **Step 1: Confirm the awscc v2 schemas**

Run:
```bash
cd /tmp/awscc-schema && terraform providers schema -json | python3 -c "
import json,sys
r=json.load(sys.stdin)['provider_schemas']['registry.terraform.io/hashicorp/awscc']['resource_schemas']
for n in ('awscc_mediapackagev2_channel_group','awscc_mediapackagev2_channel','awscc_mediapackagev2_channel_policy','awscc_mediapackagev2_origin_endpoint'):
    print(n, sorted(r[n]['block']['attributes']))
h=r['awscc_mediapackagev2_origin_endpoint']['block']['attributes']['hls_manifests']
print('hls_manifests', h.get('nested_type',{}).get('nesting_mode'), sorted(h['nested_type']['attributes']))
c=r['awscc_mediapackagev2_channel']['block']['attributes']['ingest_endpoints']
print('ingest_endpoints', c.get('nested_type',{}).get('nesting_mode'), sorted(c['nested_type']['attributes']))"
```
Expected: channel group has `channel_group_name` and `egress_domain`; channel has `channel_group_name`, `channel_name`, `input_type`, `ingest_endpoints` (with `url`); channel policy has `channel_group_name`, `channel_name`, `policy`; origin endpoint has `origin_endpoint_name`, `container_type`, `hls_manifests` (with `manifest_name`) and `hls_manifest_urls`. If a name or nesting differs, use the printed one in Step 4 and mention it in the commit message.

- [ ] **Step 2: Write the failing test**

`modules/package/tests/package.tftest.hcl`:

```hcl
mock_provider "awscc" {}

variables {
  name               = "live-demo"
  medialive_role_arn = "arn:aws:iam::123456789012:role/live-demo-medialive"
}

run "channel_accepts_hls" {
  command = plan

  assert {
    condition     = awscc_mediapackagev2_channel.this.input_type == "HLS"
    error_message = "Channel must use HLS ingest."
  }

  assert {
    condition     = awscc_mediapackagev2_origin_endpoint.hls.container_type == "TS"
    error_message = "HLS ingest uses TS segments, so the endpoint container must be TS."
  }
}

run "policy_allows_only_the_medialive_role" {
  command = plan

  assert {
    condition     = jsondecode(awscc_mediapackagev2_channel_policy.ingest.policy).Statement[0].Principal.AWS == "arn:aws:iam::123456789012:role/live-demo-medialive"
    error_message = "Only the MediaLive role may ingest."
  }

  assert {
    condition     = jsondecode(awscc_mediapackagev2_channel_policy.ingest.policy).Statement[0].Action == "mediapackagev2:PutObject"
    error_message = "Ingest policy must grant mediapackagev2:PutObject."
  }
}
```

- [ ] **Step 3: Run it to verify it fails**

Run: `cd modules/package && terraform init -backend=false -input=false && terraform test`
Expected: FAIL (no configuration).

- [ ] **Step 4: Write the implementation**

`versions.tf`:

```hcl
terraform {
  required_version = ">= 1.11"

  required_providers {
    awscc = {
      source  = "hashicorp/awscc"
      version = "~> 1.0"
    }
  }
}
```

`variables.tf`:

```hcl
variable "name" {
  description = "Base name for the channel group, channel and endpoint."
  type        = string
}

variable "medialive_role_arn" {
  description = "ARN of the MediaLive role allowed to ingest into the channel."
  type        = string
}
```

`main.tf`:

```hcl
resource "awscc_mediapackagev2_channel_group" "this" {
  channel_group_name = var.name
}

resource "awscc_mediapackagev2_channel" "this" {
  channel_group_name = awscc_mediapackagev2_channel_group.this.channel_group_name
  channel_name       = var.name
  input_type         = "HLS"
}

resource "awscc_mediapackagev2_channel_policy" "ingest" {
  channel_group_name = awscc_mediapackagev2_channel_group.this.channel_group_name
  channel_name       = awscc_mediapackagev2_channel.this.channel_name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AllowMediaLiveIngest"
        Effect    = "Allow"
        Principal = { AWS = var.medialive_role_arn }
        Action    = "mediapackagev2:PutObject"
        Resource  = awscc_mediapackagev2_channel.this.arn
      }
    ]
  })
}

resource "awscc_mediapackagev2_origin_endpoint" "hls" {
  channel_group_name   = awscc_mediapackagev2_channel_group.this.channel_group_name
  channel_name         = awscc_mediapackagev2_channel.this.channel_name
  origin_endpoint_name = "${var.name}-hls"
  container_type       = "TS"

  hls_manifests = [
    {
      manifest_name = "index"
    }
  ]
}
```

`outputs.tf`:

```hcl
output "ingest_url" {
  description = "HLS ingest URL of the MediaPackage v2 channel."
  value       = awscc_mediapackagev2_channel.this.ingest_endpoints[0].url
}

output "hls_manifest_url" {
  description = "HLS playback manifest URL of the origin endpoint."
  value       = awscc_mediapackagev2_origin_endpoint.hls.hls_manifest_urls[0]
}

output "egress_domain" {
  description = "Egress domain of the channel group."
  value       = awscc_mediapackagev2_channel_group.this.egress_domain
}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd modules/package && terraform fmt -check && terraform validate && terraform test`
Expected: `Success! 2 passed, 0 failed.`

- [ ] **Step 6: Commit**

```bash
git add modules/package
git commit -m "feat: add MediaPackage v2 package module via awscc"
```

---

### Task 4: Encode module (MediaLive input + HLS channel)

**Files:**
- Create: `modules/encode/{versions.tf,variables.tf,main.tf,outputs.tf}`
- Test: `modules/encode/tests/encode.tftest.hcl`

**Interfaces:**
- Consumes: `flow_arn` (from `modules/ingest`), `role_arn` (from `modules/medialive-role`), `ingest_url` (from `modules/package`).
- Produces: variables `name`, `flow_arn`, `role_arn`, `ingest_url` (strings), `channel_class` (string, default `SINGLE_PIPELINE`); outputs `channel_id` (string), `channel_arn` (string).

- [ ] **Step 1: Write the failing test**

`modules/encode/tests/encode.tftest.hcl`:

```hcl
mock_provider "aws" {}

variables {
  name       = "live-demo"
  flow_arn   = "arn:aws:mediaconnect:us-east-1:123456789012:flow:1-abc:live-demo"
  role_arn   = "arn:aws:iam::123456789012:role/live-demo-medialive"
  ingest_url = "https://example.ingest.mediapackagev2.us-east-1.amazonaws.com/in/v1/live-demo/1/live-demo/index.m3u8"
}

run "defaults_to_single_pipeline_and_not_started" {
  command = plan

  assert {
    condition     = aws_medialive_channel.this.channel_class == "SINGLE_PIPELINE"
    error_message = "Default channel class must be SINGLE_PIPELINE."
  }

  assert {
    condition     = aws_medialive_channel.this.start_channel == false
    error_message = "Terraform must not start the channel."
  }
}

run "input_comes_from_the_mediaconnect_flow" {
  command = plan

  assert {
    condition     = aws_medialive_input.this.type == "MEDIACONNECT"
    error_message = "Input must be a MediaConnect input."
  }
}

run "rejects_unknown_channel_class" {
  command = plan

  variables {
    channel_class = "DOUBLE"
  }

  expect_failures = [var.channel_class]
}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd modules/encode && terraform init -backend=false -input=false && terraform test`
Expected: FAIL (no configuration).

- [ ] **Step 3: Write the implementation**

`versions.tf`:

```hcl
terraform {
  required_version = ">= 1.11"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}
```

`variables.tf`:

```hcl
variable "name" {
  description = "Base name for the MediaLive input and channel."
  type        = string
}

variable "flow_arn" {
  description = "ARN of the MediaConnect flow that feeds the channel."
  type        = string
}

variable "role_arn" {
  description = "ARN of the IAM role MediaLive assumes."
  type        = string
}

variable "ingest_url" {
  description = "HLS ingest URL of the MediaPackage v2 channel."
  type        = string
}

variable "channel_class" {
  description = "MediaLive channel class. Immutable after creation; changing it replaces the channel."
  type        = string
  default     = "SINGLE_PIPELINE"

  validation {
    condition     = contains(["SINGLE_PIPELINE", "STANDARD"], var.channel_class)
    error_message = "channel_class must be SINGLE_PIPELINE or STANDARD."
  }
}
```

`main.tf`:

```hcl
resource "aws_medialive_input" "this" {
  name     = "${var.name}-input"
  type     = "MEDIACONNECT"
  role_arn = var.role_arn

  media_connect_flows {
    flow_arn = var.flow_arn
  }
}

resource "aws_medialive_channel" "this" {
  name          = var.name
  channel_class = var.channel_class
  role_arn      = var.role_arn
  start_channel = false

  input_specification {
    codec            = "AVC"
    input_resolution = "HD"
    maximum_bitrate  = "MAX_10_MBPS"
  }

  input_attachments {
    input_attachment_name = "mediaconnect-srt"
    input_id              = aws_medialive_input.this.id
  }

  destinations {
    id = "mediapackage-v2"

    settings {
      url = var.ingest_url
    }
  }

  encoder_settings {
    timecode_config {
      source = "EMBEDDED"
    }

    video_descriptions {
      name   = "video_720p"
      width  = 1280
      height = 720

      codec_settings {
        h264_settings {
          bitrate             = 3000000
          rate_control_mode   = "CBR"
          framerate_control   = "SPECIFIED"
          framerate_numerator = 30
          framerate_denominator = 1
        }
      }
    }

    audio_descriptions {
      name                = "audio_main"
      audio_selector_name = "default"

      codec_settings {
        aac_settings {
          bitrate     = 128000
          coding_mode = "CODING_MODE_2_0"
          sample_rate = 48000
        }
      }
    }

    output_groups {
      name = "hls-to-mediapackage"

      output_group_settings {
        hls_group_settings {
          segment_length = 6

          destination {
            destination_ref_id = "mediapackage-v2"
          }

          hls_cdn_settings {
            hls_basic_put_settings {}
          }
        }
      }

      outputs {
        output_name             = "720p"
        video_description_name  = "video_720p"
        audio_description_names = ["audio_main"]

        output_settings {
          hls_output_settings {
            name_modifier = "_720p"

            hls_settings {
              standard_hls_settings {
                m3u8_settings {}
              }
            }
          }
        }
      }
    }
  }
}
```

`outputs.tf`:

```hcl
output "channel_id" {
  description = "ID of the MediaLive channel."
  value       = aws_medialive_channel.this.channel_id
}

output "channel_arn" {
  description = "ARN of the MediaLive channel."
  value       = aws_medialive_channel.this.arn
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd modules/encode && terraform fmt && terraform validate && terraform test`
Expected: `Success! 3 passed, 0 failed.` If `validate` reports a missing or renamed block, fix it against the error text (the provider schema is the source of truth) and note the change in the commit message.

- [ ] **Step 5: Commit**

```bash
git add modules/encode
git commit -m "feat: add encode module (MediaLive input and HLS channel)"
```

---

### Task 5: Wire the environment and run the live spike (decision gate)

**Files:**
- Modify: `envs/demo/main.tf`, `envs/demo/outputs.tf`

**Interfaces:**
- Consumes: `ingest.flow_arn`; `medialive-role.role_arn`; `package.ingest_url`.
- Produces: outputs `medialive_channel_id`, `hls_manifest_url`.

- [ ] **Step 1: Wire the modules**

Append to `envs/demo/main.tf`:

```hcl
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
```

Append to `envs/demo/outputs.tf`:

```hcl
output "medialive_channel_id" {
  description = "ID of the MediaLive channel."
  value       = module.encode.channel_id
}

output "hls_manifest_url" {
  description = "HLS playback manifest URL from MediaPackage v2."
  value       = module.package.hls_manifest_url
}
```

- [ ] **Step 2: Init, validate and plan**

Run: `cd envs/demo && terraform init -backend-config=backend.hcl -upgrade=false && terraform fmt -check -recursive ../.. && terraform validate && terraform plan -out=tfplan`
Expected: a plan adding the ingest, role, package (group, channel, policy, endpoint) and encode (input, channel) resources, with no errors. Read the plan before applying.

- [ ] **Step 3: Apply**

Run: `cd envs/demo && terraform apply tfplan && rm -f tfplan`
Expected: apply completes. Note any error text verbatim; an API rejection here (for example the MediaLive channel refusing the destination URL or the policy) already answers the spike, so go to Step 8.

- [ ] **Step 4: Start the flow, then the channel, then push SRT**

Run:
```bash
cd envs/demo
FLOW=$(terraform output -raw flow_arn); CH=$(terraform output -raw medialive_channel_id)
aws mediaconnect start-flow --flow-arn "$FLOW" >/dev/null
until [ "$(aws mediaconnect describe-flow --flow-arn "$FLOW" --query Flow.Status --output text)" = ACTIVE ]; do sleep 5; done
aws medialive start-channel --channel-id "$CH" >/dev/null
until [ "$(aws medialive describe-channel --channel-id "$CH" --query State --output text)" = RUNNING ]; do sleep 10; done
echo "channel RUNNING"
```
Then in a separate background shell (leave it running):
```bash
cd envs/demo
IP=$(terraform output -raw ingest_ip); PORT=$(terraform output -raw ingest_port)
PASS=$(aws secretsmanager get-secret-value --secret-id "$(terraform output -raw passphrase_secret_arn)" --query SecretString --output text)
ffmpeg -re -f lavfi -i "testsrc2=size=1280x720:rate=30" -f lavfi -i "sine=frequency=1000" \
  -c:v libx264 -preset veryfast -tune zerolatency -b:v 2M -c:a aac \
  -f mpegts "srt://${IP}:${PORT}?mode=caller&passphrase=${PASS}&pbkeylen=32&pkt_size=1316"
```
Expected: the channel reaches `RUNNING` (about 1-3 minutes) and FFmpeg streams without errors.

- [ ] **Step 5: See the thumbnail in the console (manual)**

In the AWS console (us-east-1): MediaConnect → Flows → `live-sports-aws-demo` → Details page → **Preview** section. Expected: a JPEG thumbnail (480x270) of the test pattern, refreshing every few seconds. Tell the agent whether it appeared.

- [ ] **Step 6: Test whether MediaPackage v2 received the stream**

Run (after 60-90 seconds of streaming):
```bash
cd envs/demo
URL=$(terraform output -raw hls_manifest_url)
curl -s -o /tmp/manifest.m3u8 -w "HTTP %{http_code}\n" "$URL"; head -12 /tmp/manifest.m3u8
aws cloudwatch get-metric-statistics --namespace AWS/MediaLive --metric-name Output4xx \
  --dimensions Name=ChannelId,Value="$(terraform output -raw medialive_channel_id)" Name=Pipeline,Value=0 \
  --start-time "$(date -u -d '5 minutes ago' +%FT%TZ)" --end-time "$(date -u +%FT%TZ)" \
  --period 60 --statistics Sum --output table
```
Expected on SUCCESS: `HTTP 200` and a manifest starting `#EXTM3U` that lists segments; `Output4xx` empty or 0. Expected on FAILURE: `HTTP 404`/`403` or an empty manifest, and non-zero `Output4xx`. (If `Output4xx` is not the right metric name, `aws cloudwatch list-metrics --namespace AWS/MediaLive` shows the names.)

- [ ] **Step 7: Stop everything and destroy**

Run:
```bash
pkill -x ffmpeg
cd envs/demo
aws medialive stop-channel --channel-id "$CH" >/dev/null
until [ "$(aws medialive describe-channel --channel-id "$CH" --query State --output text)" = IDLE ]; do sleep 10; done
aws mediaconnect stop-flow --flow-arn "$FLOW" >/dev/null
until [ "$(aws mediaconnect describe-flow --flow-arn "$FLOW" --query Flow.Status --output text)" = STANDBY ]; do sleep 5; done
terraform destroy
aws medialive list-channels --query 'Channels[].Name' --output text
aws medialive list-inputs --query 'Inputs[].Name' --output text
aws mediaconnect list-flows --query 'Flows[].Name' --output text
aws mediapackagev2 list-channel-groups --query 'Items[].ChannelGroupName' --output text
```
Expected: `terraform destroy` completes and all four list commands print nothing.

- [ ] **Step 8: Decision gate**

- **If Step 6 succeeded (HTTP 200 with segments):** record "spike passed" with the date in the spec (verification item 3), commit, and report to the user. Plan 3 is then the full build on this path.
- **If it failed at Step 3 or Step 6:** DO NOT try workarounds and do not write Plan 3. Record the exact error, the HTTP status and the metric result in the spec (verification item 3), make sure Step 7 left nothing running, commit, and **ask the user which fallback to take** (MediaLive HLS → S3 → CloudFront, or MediaPackage v1 channel + `terraform_data`/AWS CLI).

```bash
git add envs/demo docs/superpowers/specs/2026-09-18-live-srt-pipeline-design.md
git commit -m "feat: wire delivery-path spike into demo env; record result"
```

---

## Self-Review

**Spec coverage (Plan 2 scope):** thumbnails requirement (Task 1, console check in Task 5 Step 5); delivery-path decision and its "ask the user on failure" rule (Task 5 Step 8, Global Constraints); declarative-only constraint (Global Constraints); `channel_class` variable defaulting to `SINGLE_PIPELINE` and `start_channel = false` (Task 4); MediaConnect → MediaLive input (Task 4); cleanup verification (Task 5 Step 7). Deferred to Plans 3-4: ABR ladder, CloudFront, player, tightened IAM, Python CLI, FFmpeg script, README, cost estimate.

**Placeholder scan:** none. Steps have full code or exact commands with expected output. Schema-check steps (Task 1 Step 1, Task 3 Step 1) have expected names and an explicit rule for differences.

**Type consistency:** `flow_arn`, `role_arn`, `ingest_url`, `channel_class`, `thumbnails_enabled`, `medialive_role_arn`, `hls_manifest_url`, `channel_id` and `channel_arn` are named identically in variables, outputs, tests and `envs/demo`.

**Known risks:** (1) The v2 HLS-ingest path is expected to be at risk of an auth rejection because MediaLive's HLS output has no SigV4 mode; that is exactly what the gate tests. (2) The `awscc` v2 attribute names and the channel-policy JSON shape come from CloudFormation docs and memory; Task 3 Step 1 and the live run check them. (3) The MediaLive role permissions are a best guess; a missing permission surfaces as an apply or channel-start error and is fixed in place. (4) The audio selector name `default` and the `Output4xx` metric name are unverified and have fallbacks noted.
