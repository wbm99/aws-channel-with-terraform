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
- **Operations:** a Python CLI (`livectl`) with `start`, `stop`, `status` and `check-clean`, a browser control center
  that shows every resource in the chain with its logs and runs the test source, and `just` recipes for everything;
  tested with `pytest`, `moto` and browser tests driven through every state.
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

`just ui` serves a control center on <http://127.0.0.1:8765>. It is built on Python's standard library and plain
browser modules, so it adds no dependency to the project.

- **Header:** a one-line verdict for the whole pipeline (*Not deployed*, *Off air*, *On air · playing*,
  *On air · no source*, *Partly on*, …) and what is billing per hour right now, summed from the resources that are up.
- **Pipeline:** one node per resource, left to right: SRT Input Source → MediaConnect Flow → MediaLive Input →
  MediaLive Channel → MediaPackage Channel → CloudFront CDN → Player. Each shows its AWS state, a health colour and a
  key figure. The Player node fetches the playlist through CloudFront and checks that its media sequence advances, the
  only end-to-end proof that viewers get video. Clicking a node shows everything known about it, with a link to the
  AWS console.
- **Player:** the deployed hls.js page, embedded next to the selected node. It is reloaded once when the playlist
  starts advancing, because the page gives up on the 404s it receives before the first segment exists.
- **Controls,** named for what they do and shown only when they can work:

  | Control | Runs |
  |---|---|
  | Deploy stack | `terraform apply` (refused while on air: Terraform cannot update a running channel) |
  | Tear down stack | `terraform destroy`, after you type `destroy` (refused while on air) |
  | Scan for leftovers | lists billable resources still carrying the project name |
  | Go live | starts the flow, then the channel |
  | Go off air | stops the test source, then the channel, then the flow |
  | Send test source / Stop test source | runs `source/send-srt.sh` against the ingest |

- **Endpoints:** SRT ingest, player, HLS manifest and the Secrets Manager ARN of the passphrase, each with a Copy
  button that copies exactly the value.
- **Logs panel,** one tab per resource. MediaLive and MediaConnect events (state changes, alerts, SRT source health
  with its TR 101 290 flags) reach a log group through an EventBridge rule (`modules/observability`); MediaLive's own
  encoder and as-run logs are read from `ElementalMediaLive`; the SRT Source tab also shows the test source's output.
  MediaPackage and CloudFront access logs are not enabled; their figures are in the node details.
- **Last job:** the output of the latest Deploy, Tear down, Go live, Go off air or Scan, full width under the
  endpoints, showing the newest lines with a *Show all* toggle.
- **Next step:** the button for the next step towards a playing stream (Deploy stack, then Go live, then Send test
  source) pulses, and stops once the stream plays or a source is already connected.

It is deliberately blunt about its limits:

- **Loopback only.** It refuses any address that is not `127.0.0.1`, `localhost` or `::1`, because it can destroy
  infrastructure. There is no authentication and it is not meant to be exposed.
- **The server enforces the rules, not only the page.** Hidden buttons are a convenience; a direct API call to tear
  down while on air, or without the typed word, is refused the same way.
- **One operation at a time.** A second request while a job is running is refused rather than queued.
- **The test source's passphrase** is read from Secrets Manager when you click *Send test source*, handed to FFmpeg in
  its environment, and replaced by `***` in every output line before the console stores it. Closing the console stops
  the source.
- **Cheap to watch.** AWS state is cached for 5 s and CloudWatch metrics are read in one call a minute, so an open page
  polling every 2 s costs almost nothing.
- Terraform runs with `-input=false`, so it can never stall on a prompt nobody can see.

To see every state without an AWS account, `just ui-scenario <name>` serves the console against stub clients
(`not-deployed`, `off-air`, `on-air-playing`, `on-air-no-source`, `partly-on`, … listed in `tools/tests/scenarios.py`).

### Verify the console live (costs money: about 1.74 USD/hour while on air)

Offline tests prove the console's logic, not AWS's answers. After `just up` (re-run it once on an existing stack so
the new outputs exist), open `just ui` and check:

1. Every node shows a state; none says "not in the Terraform outputs yet".
2. **Go live**: the verdict goes *Going live…* then *On air · no source*; the cost shows about $1.49 / h.
3. **Send test source**: within about two minutes the SRT node reads *connected*, the verdict *On air · playing*,
   and the player shows the burned-in clock.
4. The **MediaLive** tab shows channel state events (proves the EventBridge rule and the log resource policy) and
   encoder log lines (the `ElementalMediaLive` streams are named after the channel ARN with `_` for `:`, confirmed
   in the first live run).
5. The **SRT Source** tab shows FFmpeg output with `passphrase=***`.
6. The **CloudFront** node shows requests per minute (proves the metric dimensions).
7. Stop the test source while on air: the channel node lists an input-loss alert (proves `list_alerts` with
   `StateFilter=SET`), and the player turns black.
8. Each *Open in the AWS console* link opens the right page.
9. **Go off air**: the source stops first, then channel and flow; the verdict reads *Off air* and the cost $0.00.
10. **Tear down**, then **Scan for leftovers**: nothing billable, and `ElementalMediaLive` listed as information.

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
  observability/    EventBridge rule and log group for MediaLive and MediaConnect events
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
just test-ui       # drive the console in Chrome through every scenario (pip install -e "tools[dev,ui]")
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
- **Never act on an in-between state.** Stopping the channel makes MediaConnect update the flow for a few seconds
  (ACTIVE → UPDATING → ACTIVE). My stop logic checked during that window, saw UPDATING, never sent `stop_flow`, and
  waited ten minutes for STANDBY while the flow kept billing. `livectl` now waits for each resource to settle before
  deciding what to do, and logs every state it passes through.
- **IAM is eventually consistent, so dependencies must say so.** A rehearsal from scratch failed with a 403 because the MediaLive input was created before its role's policy existed. The role's `role_arn` output now depends on the policy. Earlier runs had only been lucky.

## Known limitations

- The demo uses one pipeline; `channel_class = "STANDARD"` is a variable but is not exercised.
- The MediaLive role uses broad `Resource = "*"` on a few actions. It is fine for a demo and should be scoped before real use.
- Delivery is MPEG-TS over HLS only.
- The latency figure is approximate: the encoder clock and the browser clock are not synchronised.
- The SRT passphrase is visible in the FFmpeg process arguments on a shared machine, whether the source runs from
  `just send` or from the console (FFmpeg takes it inside its SRT URL), and it is stored in Terraform state (the state
  bucket is private, encrypted and versioned).
- If your public IP changes, update `source_cidr` and re-apply, or the stream is rejected.
- The browser console has no authentication. It binds to loopback only and refuses anything else, but anyone with an
  account on the same machine can reach it, and its apply and destroy run with `-auto-approve`.
- MediaLive creates the `ElementalMediaLive` log group itself, so it outlives `terraform destroy`. The leftover scan
  lists it as information (log storage only, not billed by the hour).
- `livectl check-clean` covers the media resources and CloudFront (flows, channels, inputs, channel groups, distributions). It does not look at secrets, IAM roles or S3 buckets; `terraform destroy` removes those, and the README's teardown ends with it.
