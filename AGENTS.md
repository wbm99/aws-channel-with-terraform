# AGENTS.md

Guidance for AI agents working in this repository. `CLAUDE.md` is a symlink to this file.

A live SRT-to-HLS pipeline built with Terraform on AWS and operated with a small Python CLI (`livectl`). An SRT feed
enters MediaConnect, MediaLive encodes an ABR ladder, MediaPackage v2 originates, CloudFront delivers, and a static
hls.js page plays it. See `README.md` for the architecture and `docs/study-notes.md` for the reasoning behind each
choice.

## Money first

This repository creates resources that bill **by the hour while running**, not by request.

- A running demo costs **about 1.74 USD/hour** (MediaLive ~1.20, MediaConnect ~0.29, MediaPackage ~0.25, rest smaller).
  The breakdown is in `docs/cost-estimate.md`.
- **Never leave a `just start` without a `just stop`.** The flow and channel keep billing until stopped, even with no
  viewers and no source connected.
- **Always run `just check-clean` after `just down`.** It exits 1 if a billable flow, channel, input, channel group or
  distribution survived the destroy.
- **Close the Grafana dashboard when you are not watching it:** each open tab queries CloudWatch every 5 seconds
  (about 0.06-0.40 USD/hour, see `docs/cost-estimate.md`).
- **Never run `apply`, `destroy`, `start` or `stop` against a real account unless the user asked in this session.**
  Everything worth checking can be checked offline (see Testing).
- `bootstrap/` holds the $25 monthly budget with email alerts. It exists because an earlier version put the budget
  inside the demo environment and destroyed it along with everything it was meant to watch.

## Two Terraform roots

| Root | State | Lifecycle |
|---|---|---|
| `bootstrap/` | **local**, gitignored | Created once, **never destroyed**. Holds the S3 state bucket and the budget. |
| `envs/demo/` | S3 backend (`backend.hcl`, gitignored) | The disposable stack. Applied and destroyed freely. |

`bootstrap/` is separate because of the chicken-and-egg problem: the S3 backend needs its bucket to exist first. Its
state lives only on the local machine — do not add a backend to it.

## Commands

Use `just`. `just --list` is the index, and the `justfile` is the readable source of the underlying commands — read it
rather than reconstructing a `terraform -chdir=...` invocation by hand.

The ones that matter: `just test` (full offline suite), `just fmt`, `just validate`, `just status`, `just check-clean`,
`just ui` (the browser console on 127.0.0.1). With Docker, `docker compose up` starts the console and
`docker compose run --rm console just <recipe>` runs the recipes that need only Terraform or livectl (see
*Containers*).

## Terraform conventions

- **One module per layer** under `modules/`: `ingest`, `medialive-role`, `encode`, `package`, `player`, `delivery`,
  `guardrails`, `observability`. `envs/demo/main.tf` wires them together and is the only place they meet.
- Every module carries `versions.tf`, `variables.tf`, `outputs.tf`, `main.tf` and `tests/<name>.tftest.hcl`. A new
  module without a test file is incomplete.
- `description` on **every** variable and output. No exceptions in this repo so far; keep it that way.
- Run `terraform fmt -recursive` before committing (`just fmt`).
- **Use `hashicorp/awscc` for MediaConnect and MediaPackage v2.** The `hashicorp/aws` provider has no resources for
  them, and MediaPackage v1 has no origin endpoint resource at all. See `modules/ingest` and `modules/package`. Do not
  "fix" these to `aws_*` resources — they do not exist.
- Naming flows from `var.project` (`live-sports-aws`) plus the environment: `${var.project}-demo`. `livectl check-clean`
  matches resources on that prefix, so renaming breaks the leftover check.

## Python conventions (`tools/livectl/`)

- **boto3 is the only runtime dependency. Keep it that way.** The console is built on `http.server` from the standard
  library for exactly this reason. `pytest` and `moto` are dev-only extras.
- **AWS clients, subprocess runners and clocks are passed in as parameters, never constructed inside logic.** This is
  the single most important convention here — it is what makes the test suite work with no credentials and no network:
  - `control.start(mediaconnect, medialive, targets, ...)` takes clients, not a session.
  - `targets.Runner` is an injectable `Callable[[Sequence[str]], str]`, so tests hand in a fake instead of running
    `terraform`.
  - `control.wait_for` takes `sleep` and `clock`, so timeout tests run instantly (`NO_SLEEP` in `tests/test_control.py`).
  - Only `cli.py` builds a real `boto3.Session`.
- `from __future__ import annotations` at the top, a module docstring on every file, frozen dataclasses for value
  objects, and custom exceptions (`TargetError`, `WaitTimeout`) that `cli.main` turns into exit code 2.
- `SourceSettings` and `CHOICES` in `livectl/source.py` hold the allowed test-source values; `source/send-srt.sh`
  mirrors them (its header says so, and `test_send_script.py` checks the two agree). Change both together.
- Operations must be **idempotent**: `start` and `stop` check current state before acting, so running either twice is
  safe. There is a test for this; do not regress it.
- **The console binds to loopback only.** `livectl ui --container` additionally allows `0.0.0.0`, because Docker
  delivers published ports to the container's own interface, never its loopback. The guarantee then lives in
  `compose.yaml`'s port line, `"127.0.0.1:${CONSOLE_PORT:-8765}:8765"`: **never drop the `127.0.0.1:` prefix**, or the
  console, which can destroy the stack, is reachable from the network.
- Credentials are checked through an injected STS client (`livectl/identity.py`): `GetCallerIdentity` needs no IAM
  permission, so it tells credential problems apart from permission problems. Unusable credentials refuse every AWS
  action in `actions.refusals`.

## Containers

- One image, `docker/Dockerfile`: target `runtime` (published to `ghcr.io/wbm99/aws-channel-with-terraform`) holds the
  tools only, with **no dev dependencies**; target `test` adds pytest and moto for CI. Playwright is in neither.
- The checkout is bind-mounted at `/work`, so state, `backend.hcl` and tfvars stay where the host path keeps them.
  `~/.aws` is mounted read-write, because SSO saves refreshed tokens in `~/.aws/sso/cache`.
- The image's own CMD binds loopback; only compose passes `--container --host 0.0.0.0`. Keep it that way, so a plain
  `docker run -p 8765:8765` of the published image cannot expose the console.
- The image sets `TF_DATA_DIR=.terraform-docker`, so the container's provider binaries (built for Linux) never mix with
  the host's `.terraform/`. `LIVECTL`, `LIVECTL_PYTHON` and `LIVECTL_PYTEST` point the justfile at the installed
  tools (prefixed, because a host often exports `PYTHON` for other tools).
- `source.SCRIPT` and `--tf-dir` are relative to the working directory: an installed livectl is far from the checkout.
- CI (`.github/workflows/image.yml`) runs `just test-tf test-py` in the `test` image and `just test-ui` and
  `just test-grafana` on the runner on every pull request, and publishes the image on merge to `main`.
- Compose also runs `grafana` (`grafana/grafana-oss`, pinned): provisioning from `observability/grafana/`, `~/.aws`
  mounted **read-only** (Grafana never writes credentials), as the host UID so it can read `~/.aws/credentials`. Its
  port line is `"127.0.0.1:${GRAFANA_PORT:-3000}:3000"`; the same `127.0.0.1:` rule applies, since it holds working
  AWS credentials. `LIVECTL_DOCKER` points the justfile at a fake Docker in tests.

## Testing

Everything runs **offline, with no AWS credentials**:

```
just test        # terraform test for bootstrap + all modules, then pytest over tools
```

- Terraform tests use mocked providers, so they prove the configuration is self-consistent — wiring, validation,
  defaults. They do **not** prove AWS accepts the request. Three things have passed mocked tests and been rejected live;
  they are listed under *Lessons learned* in `README.md`.
- Python tests use `moto`. Fixtures are in `tools/tests/conftest.py`; `make_workflow()` builds a flow and a channel.
  Where moto has no implementation (MediaLive alerts, MediaPackage v2 endpoints), `tools/tests/stubs.py` answers instead.
- The console page is tested in a real browser: `tools/tests/scenarios.py` runs the server in named states against
  stub clients, and `just test-ui` drives Chrome through each one with Playwright (a dev-only extra, `tools[ui]`).
  `just test` runs these too when Playwright is installed and skips them otherwise. `just ui-scenario <name>` shows a
  state by hand.

## Secrets

- `*.tfvars`, `backend.hcl`, all `*.tfstate*` and `.venv/` are gitignored. Check `.gitignore` before adding a file that
  might carry account details.
- The SRT passphrase is generated by Terraform, stored in Secrets Manager and read at runtime. **Never write it into a
  file, a default value or a committed command line.** `just send` resolves it into an environment variable at the
  moment of use. The console shows it only on request (`POST /api/source/connection`, for pasting into OBS), only to a
  loopback `Host` header, and never in the polled payload or the logs; tests check all three.
- It is present in Terraform state in plain text — that is a known, documented limitation, mitigated by the private,
  encrypted, versioned state bucket. Do not present it as solved.

## Traps already paid for

`README.md` has the full *Lessons learned* list. The two that bite agents editing code:

- **IAM is eventually consistent.** `modules/medialive-role` makes its `role_arn` output depend on the policy, so
  MediaLive cannot be created before its permissions exist. Removing that dependency reintroduces an intermittent 403
  that only appears on a from-scratch apply.
- **Empty HCL blocks are silently dropped.** An empty `hls_basic_put_settings {}` disappears, and MediaLive then rejects
  the request with a 422 that does not mention it.

## Commits

Conventional Commits, matching the existing history: `feat:`, `fix:`, `docs:`. Subject in the imperative, lower case
after the prefix.
