# Containers: Design

Date: 2026-09-28
Status: Approved; planned in `docs/superpowers/plans/2026-09-28-plan-8-containers.md`
Branch: `feat/containers` (stacked on `feat/control-center`)

This document has two parts: a product part (PRD: what and why) and a technical part (TRD: how).

---

# Part 1: PRD

## Purpose
Let someone try the project on their own AWS account with Docker as the only install: no Terraform, Python, FFmpeg,
`just` or AWS CLI on the host. The whole lifecycle is covered: bootstrap, deploy, go live, test source, tear down,
scan for leftovers.

## Audience
A reader of the article or a reviewer who has cloned the repository. The same image also serves as a reproducible
toolbox for the author, and CI runs the offline test suite inside it.

## Goals
1. Four commands from clone to console: `git clone`, one-time `bootstrap` and `init` through `docker compose run`, then
   `docker compose up`.
2. The Docker path and the host (`just`) path are interchangeable on the same checkout.
3. The console is reachable only from the host machine, never from the network, exactly as it is today.
4. Credential mistakes (no profile, expired SSO token, bad keys, root user) are named on screen with the fix, not shown
   as a grey chain of repeated errors.
5. A missing permission names the denied action.
6. Every pull request runs the offline suite in CI; every merge to `main` publishes the image.
7. The published image is as light as the tools allow, with no dev or test dependencies.

## Success criteria
- On a machine with only Docker and a configured AWS profile, the four commands bring up the console at
  `http://127.0.0.1:8765`, and every console action works.
- `CONSOLE_PORT=9000` in `.env` moves the console to port 9000 with no other change.
- The console cannot be reached through the host's LAN address.
- `livectl ui --host 0.0.0.0` without `--container` is still refused.
- Scenario tests show the identity line, the root banner, and the no-credentials banner in a browser.
- A PR with a failing test shows a failed check; a merge to `main` publishes `:latest` and `:sha-<short>` for amd64 and
  arm64.

## Non-goals
- **A scoped IAM policy.** It needs its own spec and a live verification run. Today the stack assumes an admin-level
  IAM user or SSO role (never root).
- **InfluxDB and Grafana.** They will be added later as more services in the same compose file; nothing here needs to
  change for that.
- **Separate UI and backend containers.** The console serves its static site and its API from one process. Splitting
  them would add nginx, a proxy and a second image for no gain at this scale.
- **Version tags** (`:1.0.0`). They can be added to the same workflow once releases exist.
- **Native Windows** without WSL. Bind mounts and `${HOME}` behave differently there.
- **The AWS CLI in the image.** Credentials are set up on the host.

---

# Part 2: TRD

## Image

One image, `ghcr.io/wbm99/aws-channel-with-terraform`, built from `docker/Dockerfile`. It contains tools only. The
project files come from a bind mount of the checkout.

| Layer | Source | Notes |
|---|---|---|
| Base | `python:3.12-alpine` | boto3 is pure Python, so musl is not a concern |
| Terraform | binary copied from `hashicorp/terraform:1.16` | static Go binary; repo requires `>= 1.11` |
| FFmpeg, font | `apk add ffmpeg font-dejavu` | image sets `FONT=/usr/share/fonts/dejavu/DejaVuSans.ttf` for `source/send-srt.sh` |
| just | `apk add just` | |
| livectl | `pip install --no-cache-dir ./tools` | runtime dependencies only (boto3) |

Expected size: around 300 MB, dominated by Terraform, botocore and FFmpeg.

The container runs as a non-root user. Compose sets it at run time with `user: "${UID:-1000}:${GID:-1000}"`, so the
published image works for any host user without a rebuild, and files it writes into the bind mount (bootstrap state,
Terraform data directories) belong to the host user. `HOME=/home/app` is writable by any UID.

Alpine has no bash; the image adds it, because the justfile and `source/send-srt.sh` need it. Verified on 2026-09-28:
Alpine's FFmpeg has `srt` and every test-pattern filter, and `hashicorp/terraform:1.16` ships amd64 and arm64.

The image sets `LIVECTL=livectl` and `LIVECTL_PYTHON=python`; the `test` target also sets `LIVECTL_PYTEST=pytest` (see *justfile*).

### Targets
- `runtime`: what users pull.
- `test`: `runtime` plus `pip install ./tools[dev]` (pytest, moto). Used by CI only, never published.

Playwright and Chromium are in neither target.

## Compose

`compose.yaml` at the repository root:

```yaml
services:
  console:
    image: ghcr.io/wbm99/aws-channel-with-terraform:latest
    build: { context: ., dockerfile: docker/Dockerfile, target: runtime }
    command: livectl ui --host 0.0.0.0 --container --no-browser
    working_dir: /work
    ports:
      # Keep the 127.0.0.1 prefix: without it the console, which can destroy the stack, is reachable from the LAN.
      - "127.0.0.1:${CONSOLE_PORT:-8765}:8765"
    volumes:
      - .:/work
      - ${HOME}/.aws:/home/app/.aws
    environment:
      AWS_PROFILE: ${AWS_PROFILE:-default}
      CONSOLE_URL: http://127.0.0.1:${CONSOLE_PORT:-8765}/
```

The `~/.aws` mount uses the long syntax with `create_host_path: false`, so a missing `~/.aws` stops Compose instead of
creating a root-owned directory on the host. `CONSOLE_URL` lets the console print the host URL at startup instead of
`http://0.0.0.0:8765/`. The full file, with the `user:` line, is in plan 8.

- **Port configuration.** `.env` is optional and already gitignored. `${CONSOLE_PORT:-8765}` means "`CONSOLE_PORT` if
  set, else 8765". The port inside the container is always 8765. `.env.example` documents `CONSOLE_PORT`,
  `AWS_PROFILE`, `UID` and `GID`.
- **Credentials.** `~/.aws` is mounted read-write, because SSO saves refreshed tokens in `~/.aws/sso/cache` (a
  read-only mount was tried and broke the refresh; see plan 8's review fixes); `AWS_PROFILE` passes through from the host or `.env`. The region is
  not an environment setting here: it is Terraform's `region` variable and livectl's `--region` (both `us-east-1`).
  Static keys, profiles and IAM Identity Center all work; for SSO the user runs `aws sso login` on the host, and the
  cached token is read from the mount. Users without the AWS CLI configure the profile once with
  `amazon/aws-cli` run as their own UID with `HOME` pointing at the mount (the README has the exact command). Environment-variable keys
  work through the SDK's normal chain but are not the documented path.
- **Terraform data directory.** `TF_DATA_DIR=.terraform-docker` (set in the image, so `docker compose run` and CI get it) gives each Terraform root a separate provider directory
  for the container, because provider binaries in `.terraform/` are built for the host OS. `.terraform-docker/` is
  added to `.gitignore`. The lock file (`.terraform.lock.hcl`) is shared and unaffected.
- **State.** `bootstrap/terraform.tfstate`, `envs/demo/backend.hcl` and `*.tfvars` stay in the checkout, exactly where
  the host path keeps them. `docker compose down -v` cannot remove them.

## Commands

```
git clone https://github.com/wbm99/aws-channel-with-terraform && cd aws-channel-with-terraform
docker compose run --rm console just bootstrap   # once per AWS account
docker compose run --rm console just init        # once per checkout
docker compose up                                # console on http://127.0.0.1:8765
```

Everything after that is done in the console. Any other recipe runs the same way:
`docker compose run --rm console just check-clean`.

`docker compose down` stops the console only. **It does not stop AWS billing.** The README says so next to the
commands: use *Go off air* or *Tear down stack* first, then *Scan for leftovers*.

## livectl changes

### Binding inside a container
Inside a container, the console must listen on `0.0.0.0`: Docker's port forwarding delivers traffic to the container's
`eth0`, never to its own loopback. The loopback guarantee moves to the compose port line (`127.0.0.1:` prefix).

- `livectl ui --container` allows `--host 0.0.0.0` in addition to the loopback addresses. No other address is allowed,
  with or without the flag.
- `cli._loopback` and `server.make_server` take the flag into account. Without it, the current behaviour is unchanged.
- Tests: `0.0.0.0` is refused without `--container`; accepted with it; a LAN-style address (`192.168.1.5`) is refused
  with it.
- AGENTS.md and the comment on the compose port line explain why the prefix must stay.

### Identity check
New module `livectl/identity.py`:

```python
def whoami(sts, region: str) -> Identity
```

The STS client is injected, never built inside. `sts:GetCallerIdentity` needs no IAM permission, so it separates
credential problems from permission problems. `Identity` is a frozen dataclass with a `kind`:

| kind | Detected by | Console shows |
|---|---|---|
| `ok` | call succeeds, ARN not root | header: `acting as <user or role> · account <id> · <region>` |
| `root` | ARN ends in `:root` | the header, plus a red banner: root credentials, use an IAM user or role. Actions stay enabled. |
| `no-credentials` | `NoCredentialsError` | banner: no AWS credentials, with the profile setup commands |
| `expired` | `ExpiredToken`, `ExpiredTokenException`, `TokenRetrievalError`, `UnauthorizedSSOTokenError` | banner: run `aws sso login --profile <profile>` on the host |
| `invalid` | `InvalidClientTokenId`, `SignatureDoesNotMatch` | banner: the keys are wrong or deactivated |

- It is served as an `identity` field on `/api/pipeline`, cached through the existing cache.
- Any other failure (network down, throttling) is `unverified`: usable, header `identity not verified`, no banner, so
  the existing stale-data handling applies.
- `ProfileNotFound` (an `AWS_PROFILE` that is not in `~/.aws/config`) is raised while the clients are built, before
  the console exists, so it cannot be a banner: every command, `ui` included, exits 2 with one line naming the profile
  and the fix.
- When the kind is not usable, the chain is not polled. One banner replaces a chain of identical errors.
- `livectl status` and `livectl check-clean` print the identity line first, and exit 2 (as for `TargetError`) when
  there is no usable identity.
- `cli.py` builds the STS client along with the others.

### Named denials
In `pipeline._read`, errors whose code is `AccessDenied`, `AccessDeniedException` or `UnauthorizedOperation` become
`access denied: <service>:<Operation>` on the node, with the service and operation names read from the botocore error.
Every other error keeps its current text.

### justfile
```
livectl := env("LIVECTL", ".venv/bin/livectl")
python  := env("LIVECTL_PYTHON", ".venv/bin/python")
pytest  := env("LIVECTL_PYTEST", ".venv/bin/pytest")
```

`test-py` and `test-ui` use `{{pytest}}` instead of `{{venv}}/bin/pytest`. The `runtime` target sets
`LIVECTL=livectl` and `LIVECTL_PYTHON=python`; the `test` target adds `LIVECTL_PYTEST=pytest`, since only it has pytest. On the host,
nothing changes.

### Test source script path
`source.py` finds `send-srt.sh` as `Path(__file__).parents[2] / "source" / "send-srt.sh"`, which only works for an
editable install from the checkout. In the image livectl is installed into site-packages, so that path does not exist.
`SCRIPT` becomes `Path("source/send-srt.sh")`, relative to the working directory, the same way `--tf-dir` defaults to
`envs/demo`. `just` runs from the repository root and the container from `/work`, so both resolve it. A test asserts
that the default is relative, and the CI smoke check runs `source/send-srt.sh --frame` in the image from `/work`.

### Version skew
The livectl code comes from the image, while the Terraform code and `send-srt.sh` come from the checkout. A checkout
much newer or older than `:latest` can disagree with it. The README says to use `docker compose up --build` when
working on a checkout other than current `main`.

## CI

`.github/workflows/image.yml`, on `pull_request` and on `push` to `main`:

| Job | Runs on | Steps |
|---|---|---|
| `test` | every trigger | build the `test` target with `UID`/`GID` set to the runner's (`id -u`, `id -g`); run it with the checkout mounted at `/work` and run `just test-tf test-py` |
| `ui` | every trigger | on the runner, not in the image: `just venv`, `.venv/bin/pip install -e "tools[dev,ui]"`, `google-chrome --version` (the runner ships Chrome, which the tests launch with `channel="chrome"`), `just test-ui` |
| `publish` | `push` to `main`, after `test` and `ui` | build `runtime` for `linux/amd64,linux/arm64`; smoke-check it; push to GHCR |

- **Smoke check** on the runtime image, with the checkout mounted at `/work`: `terraform version`,
  `ffmpeg -protocols | grep -q srt`, `source/send-srt.sh --frame /tmp/f.png`, `livectl --help`, and
  `livectl ui --container --host 0.0.0.0 --no-browser` answering `GET /` with 200.
- **Tags:** `:latest` and `:sha-<short commit>`.
- **Auth:** the workflow's `GITHUB_TOKEN` with `packages: write`. No stored secrets. The repository has no AWS secrets,
  so a test that reached AWS by mistake would fail instead of billing.
- **Cache:** `docker/build-push-action` with the GitHub Actions cache backend.

## Testing

- `identity.py`: moto for the `ok` case; `tools/tests/stubs.py` gains an STS stub raising each error shape for the
  other kinds, including a root ARN.
- `pipeline._read`: a stubbed `AccessDeniedException` becomes `access denied: medialive:DescribeChannel`.
- Binding: the three cases under *Binding inside a container*.
- Scenarios (`tools/tests/scenarios.py`) and Playwright: new `no-credentials` and `root-identity` states; the identity
  line is asserted in an existing `on-air-playing` scenario.
- The image itself is tested by the CI smoke check. `docker compose up` on a real account is part of the README's
  live checklist, which costs money and is the user's to run.

## Documentation

- **README:** a *Run with Docker* section beside the existing Quick start (which stays), with the four commands, the
  profile setup without the AWS CLI, `.env` for ports, and the billing warning about `docker compose down`.
- **AGENTS.md:** the `--container` rule and the `127.0.0.1:` port prefix; no dev dependencies in the runtime image;
  `TF_DATA_DIR=.terraform-docker`; CI exists and runs `just test`.
- **`.env.example`:** `CONSOLE_PORT`, `AWS_PROFILE`, `UID`, `GID`, each commented.

## Files

| New | Changed |
|---|---|
| `docker/Dockerfile` | `tools/livectl/cli.py` (flag, STS client, identity line) |
| `compose.yaml` | `tools/livectl/server.py` (binding rule, `identity` field) |
| `.env.example` | `tools/livectl/actions.py` (refusals without credentials), `tools/livectl/pipeline.py` (named denials, skip polling without identity) |
| `.dockerignore` (`.venv`, `.git`, `.terraform*`, `**/__pycache__`) | `tools/livectl/site/*` (identity line, banners) |
| `.github/workflows/image.yml` | `tools/tests/stubs.py`, `tools/tests/scenarios.py`, tests |
| `tools/livectl/identity.py`, `tools/tests/test_identity.py` | `tools/livectl/source.py` (script path), `justfile`, `.gitignore`, `README.md`, `AGENTS.md` |
