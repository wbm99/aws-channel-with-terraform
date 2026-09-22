# Live SRT to HLS on AWS

A live-sports-style contribution and distribution pipeline built with Terraform on AWS, operated with a small Python CLI.
An SRT feed (as you would receive from a stadium) goes into AWS, is encoded into an ABR ladder, packaged, delivered through a CDN
and played in a browser, all created and destroyed with a single `terraform apply` and `terraform destroy`.

```mermaid
flowchart LR
    A["FFmpeg<br/>1080p + UTC clock"] -- "SRT, encrypted" --> B["MediaConnect<br/>SRT listener"]
    B --> C["MediaLive<br/>1080p / 720p / 480p"]
    C -- "HLS (6 s segments)" --> D["MediaPackage v2<br/>origin, CDN auth"]
    D --> E["CloudFront"]
    E --> F["Browser<br/>hls.js player"]
    G["S3 player page"] --> E
```

| Stage | What it does |
|---|---|
| **FFmpeg source** | A test pattern with a burned-in UTC clock, pushed as an encrypted SRT stream (`source/send-srt.sh`). |
| **MediaConnect** | The contribution layer. An SRT listener that accepts only your IP, decrypts with a Terraform-generated passphrase, and shows source thumbnails in the console. |
| **MediaLive** | Encodes the ladder (1080p 5 Mbps, 720p 3 Mbps, 480p 1.5 Mbps, 30 fps, 2 s GOP) and cuts each rendition into 6-second HLS segments. |
| **MediaPackage v2** | The origin. It receives the segments, keeps a rolling live window and serves the manifests and segments only to requests that carry a secret header. |
| **CloudFront** | Caches manifests briefly (2 s) and segments longer (60 s), adds the secret header on the way to the origin, and also serves the player page from a private S3 bucket. |
| **Player** | A static hls.js page that plays the stream and shows the browser clock, the player-reported latency and the current rendition. |

## What this demonstrates

- **Infrastructure as code:** one module per layer, remote state in S3 with native locking, tagging, `terraform fmt` and mocked-provider `terraform test` suites that need no AWS access.
- **Working around provider gaps:** the `hashicorp/aws` provider has no MediaConnect flow and no MediaPackage v2 resources, so those use `hashicorp/awscc` (the whole chain stays declarative).
- **Security:** SRT encryption, an IP allow-list, no public origin (CDN authorization), private buckets with origin access control, no secrets in the repository.
- **Operations:** a Python CLI (`livectl`) with `start`, `stop`, `status` and `check-clean`, a browser console for the
  same operations, and `just` recipes for everything; tested with `pytest` and `moto`.
- **Cost awareness:** a persistent budget with alerts, a priced estimate ([docs/cost-estimate.md](docs/cost-estimate.md)) and a leftover check.

## Prerequisites

- Terraform 1.11 or newer, and the AWS CLI v2 with credentials for a personal or sandbox account.
- FFmpeg built with SRT support (`ffmpeg -protocols | grep srt`) and the DejaVu Sans font (`fonts-dejavu-core`).
- Python 3.10 or newer with `virtualenv` (`pip install --user virtualenv`).
- [`just`](https://github.com/casey/just) to run the recipes below (`cargo install just`, `brew install just`, or a
  prebuilt binary from the releases page; Ubuntu ships it from 24.04 onwards).

## Quick start

Every command is a `just` recipe. `just --list` shows them all, and the [`justfile`](justfile) is the readable source
of what each one runs.

```bash
# 1. One-time: state bucket and the monthly budget (state stays local and is gitignored)
cp bootstrap/terraform.tfvars.example bootstrap/terraform.tfvars    # set alert_email
just bootstrap                                     # prints the state bucket name

# 2. Configure and create the demo environment
cp envs/demo/backend.hcl.example envs/demo/backend.hcl              # put the bucket name in
printf 'source_cidr = "%s/32"\n' "$(curl -s https://checkip.amazonaws.com)" > envs/demo/terraform.tfvars
just venv                                          # .venv with livectl and its dev extras
just init
just up                                            # a few minutes, CloudFront included

# 3. Go live
just start                                         # flow first, then the channel (about 2 minutes)
just send                                          # FFmpeg test pattern; leave it running
just player                                        # the URL to open in a browser

# 4. Tear down (do not skip: MediaLive and MediaConnect bill by the hour while running)
just stop
just down                                          # destroy, then verify nothing billable survived
```

**Without `just`:** every recipe is an ordinary shell command, so read the `justfile` and run them directly. `just send`
is the one worth copying rather than retyping: it reads the ingest address from `terraform output` and the passphrase
from Secrets Manager at the moment of use, so the secret never lands in a file or your shell history.

`livectl` reads the flow ARN and channel ID from `terraform output` by default. If Terraform state is not available,
pass them directly: `livectl stop --flow-arn <arn> --channel-id <id>`.

## Operating it

`just ui` serves a console on <http://127.0.0.1:8765>: pipeline state, what it costs per hour while it runs, the ingest
and player endpoints, and buttons for start, stop, check-clean, apply and destroy with the output streamed as it
arrives. It is built on Python's standard library, so it adds no dependency to the project.

It is deliberately blunt about its limits:

- **Loopback only.** It refuses any address that is not `127.0.0.1`, `localhost` or `::1`, because it can destroy
  infrastructure. There is no authentication and it is not meant to be exposed.
- **Destroy needs confirmation.** You type `destroy` into the page, and the server checks that token as well, so calling
  the API directly does not skip the guard.
- **One operation at a time.** A second request while a job is running is refused rather than queued.
- Terraform runs with `-input=false`, so it can never stall on a prompt nobody can see.

## Repository layout

```
AGENTS.md           conventions and cost rules for agents working here (CLAUDE.md symlinks to it)
justfile            every command in the project; `just --list` to see them
bootstrap/          one-time root: state bucket and the budget (never destroyed)
modules/
  ingest/           MediaConnect SRT flow, passphrase secret, IP allow-list, thumbnails
  medialive-role/   IAM role that MediaLive assumes
  encode/           MediaLive input and channel; the ABR ladder is a variable
  package/          MediaPackage v2 channel, origin endpoint and CDN authorization
  player/           private S3 bucket with the hls.js page
  delivery/         CloudFront distribution with the two origins
  guardrails/       AWS Budgets alerts
envs/demo/          wires the modules together
tools/livectl/      Python CLI and the browser console (site/), with tests in tools/tests
source/             FFmpeg SRT source script
scripts/            price lookup for the cost estimate
docs/               cost estimate and study notes; the spec and plans live under docs/superpowers/
```

## Testing

Everything runs offline with no AWS credentials:

```bash
just test          # terraform test for every root (mocked providers), then pytest over tools (moto)
just validate      # formatting check and terraform validate for every root
just frame         # renders one frame to /tmp/clock.png to check the burned-in clock
```

## Costs

While the demo runs, the priced items come to about **1.74 USD per hour** (about 0.43 for a 15-minute demo) for a single-pipeline
channel. When nothing is running, cost is close to zero. Details, assumptions and what could not be priced are in
[docs/cost-estimate.md](docs/cost-estimate.md). A $25 monthly budget with alerts lives in `bootstrap/`.

## Lessons learned

- **Provider gaps are normal in media services.** MediaConnect and MediaPackage v2 needed the `awscc` provider. MediaPackage v1 has no
  origin endpoint resource at all.
- **Prediction versus test.** I expected MediaLive's plain HLS output to be rejected by MediaPackage v2 (its ingest is normally authorised with
  SigV4). It works, so I ran it before deciding.
- **Small provider traps:** an empty `hls_basic_put_settings {}` block is silently dropped and MediaLive rejects the request;
  MediaConnect rejects an explicit `algorithm` when the key type is `srt-password`.
- **A silent FFmpeg failure.** My first burned-in clock used `%{gmtime\:%H\\\:%M\\\:%S}`; FFmpeg printed `Stray %` and drew nothing, while the stream
  kept running. `%{gmtime\:%T}` works. The lesson is to render one frame locally before going live (`--frame`).
- **A deleted MediaLive channel lingers in `DELETING`.** `livectl check-clean` ignores it, or it would raise a false alarm right after a destroy.
- **Budgets belong outside the environment they watch.** Mine started in the demo and was destroyed with it.
- **IAM is eventually consistent, so dependencies must say so.** A rehearsal from scratch failed with a 403 because the MediaLive input was created before its role's policy existed. The role's `role_arn` output now depends on the policy. Earlier runs had only been lucky.

## Known limitations

- The demo uses one pipeline; `channel_class = "STANDARD"` is a variable but is not exercised.
- The MediaLive role uses broad `Resource = "*"` on a few actions. It is fine for a demo and should be scoped before real use.
- Delivery is MPEG-TS over HLS only.
- The latency figure is approximate: the encoder clock and the browser clock are not synchronised.
- The SRT passphrase is visible in the FFmpeg process arguments on a shared machine, and it is stored in Terraform state
  (the state bucket is private, encrypted and versioned).
- If your public IP changes, update `source_cidr` and re-apply, or the stream is rejected.
- The browser console has no authentication. It binds to loopback only and refuses anything else, but anyone with an
  account on the same machine can reach it, and its apply and destroy run with `-auto-approve`.
- `livectl check-clean` covers the media resources and CloudFront (flows, channels, inputs, channel groups, distributions). It does not look at secrets, IAM roles or S3 buckets; `terraform destroy` removes those, and the README's teardown ends with it.
