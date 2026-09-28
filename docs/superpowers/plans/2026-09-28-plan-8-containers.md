# Containers Implementation Plan (Plan 8)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the whole project with Docker as the only install: one image with Terraform, Python/livectl, FFmpeg and `just`; `docker compose up` for the console; CI that tests every PR and publishes the image on merge to `main`; and a console that names credential problems instead of showing a grey chain.

**Architecture:** The image holds tools only; the checkout is bind-mounted at `/work`, so state and config stay where the host path keeps them. The console listens on `0.0.0.0` inside the container only with `--container`, and Compose publishes it on the host's `127.0.0.1` only. A new `livectl/identity.py` classifies `sts:GetCallerIdentity` into ok / root / no-credentials / expired / invalid / unverified; the server serves it on `/api/pipeline` and refuses AWS actions while it is unusable.

**Tech Stack:** Docker (Alpine), Docker Compose, GitHub Actions + GHCR; Python >= 3.10, boto3, pytest, moto; JavaScript modules; Playwright for Python (Google Chrome).

**Spec:** `docs/superpowers/specs/2026-09-28-containers-design.md`
**Depends on:** Plans 5-7 (branch `feat/control-center`).

## Global Constraints

- boto3 stays the only runtime dependency of livectl. The `runtime` image target contains no pytest, moto or Playwright.
- AWS clients, runners and clocks are injected; only `cli.py` builds a `boto3.Session`.
- Without `--container`, the console binds to loopback only, exactly as today. With it, `0.0.0.0` is the only extra address.
- The Compose port line keeps its `127.0.0.1:` prefix: `"127.0.0.1:${CONSOLE_PORT:-8765}:8765"`.
- Verified on 2026-09-28: `python:3.12-alpine` + `apk add ffmpeg font-dejavu just` gives FFmpeg with `srt`, `testsrc2`, `smptehdbars`, `pal100bars`, `drawtext`; the font is at `/usr/share/fonts/dejavu/DejaVuSans.ttf`; `just` 1.48. `hashicorp/terraform:1.16` (1.16.4) has `/bin/terraform` for amd64 and arm64. Alpine has no bash; the image adds it.
- The region is Terraform's `region` variable and livectl's `--region` (both default `us-east-1`). `AWS_REGION` is not passed through; do not advertise it.
- Every task ends with `just test` green (Playwright runs when installed) and a commit in Conventional Commits style.
- Nothing in this plan touches a real AWS account.

## Review Focus

1. `AWS_PROFILE` names a profile that does not exist → every command, `ui` included, exits 2 with one line naming the profile and the fix, no traceback. (Task 4)
2. The SSO token expires while the console runs, then the user runs `aws sso login` on the host → the console recovers within `IDENTITY_TTL` (15 s) with no restart. (Task 5)
3. `~/.aws` does not exist on the host → Compose refuses to start instead of creating a root-owned `~/.aws`. (Task 7)
4. Files the container writes into the checkout (bootstrap state, `.terraform-docker/`) belong to the host user. (Task 8, smoke check)
5. `CONSOLE_PORT=9000` → the startup line says `http://127.0.0.1:9000/`, not `http://0.0.0.0:8765/`. (Task 3)

## File Structure

```
tools/livectl/source.py            (modify: script path relative to the working directory)
justfile                           (modify: LIVECTL / PYTHON / PYTEST overrides)
tools/livectl/server.py            (modify: container binding, identity in the payload, refusals)
tools/livectl/cli.py               (modify: --container, CONSOLE_URL, STS client, identity line, ProfileNotFound)
tools/livectl/identity.py          (create)
tools/livectl/actions.py           (modify: Situation.credentials)
tools/livectl/pipeline.py          (modify: named denials)
tools/livectl/site/{index.html,app.js,app.css}   (modify: identity chip, identity banner)
tools/tests/{stubs.py,scenarios.py,test_identity.py,test_server.py,test_cli.py,test_pipeline.py,test_source.py,test_ui.py}
docker/Dockerfile, .dockerignore, compose.yaml, .env.example   (create)
.gitignore                         (modify: .terraform-docker/)
.github/workflows/image.yml        (create)
README.md, AGENTS.md               (modify)
```

---

### Task 1: Test source script path and justfile overrides

**Files:**
- Modify: `tools/livectl/source.py:22`, `justfile`
- Test: `tools/tests/test_source.py`

**Interfaces:**
- Produces: `livectl.source.SCRIPT == Path("source/send-srt.sh")`; justfile variables `livectl`, `python`, `pytest` read from `LIVECTL`, `PYTHON`, `PYTEST`.

- [ ] **Step 1: Write the failing test** in `tools/tests/test_source.py`

```python
def test_the_script_is_found_from_the_working_directory_not_the_install():
    from livectl.source import SCRIPT
    assert SCRIPT == Path("source/send-srt.sh")
    assert not SCRIPT.is_absolute()
```

- [ ] **Step 2: Run it:** `.venv/bin/pytest -q tools/tests/test_source.py` → FAIL (SCRIPT is absolute).
- [ ] **Step 3: Implement.** `SCRIPT = Path("source/send-srt.sh")` with a comment: relative like `--tf-dir`, because an installed (non-editable) livectl lives in site-packages; `just` runs from the repo root and the container from `/work`.
- [ ] **Step 4: justfile.** Replace the `livectl` and `python` definitions and add `pytest`:

```
livectl := env("LIVECTL", ".venv/bin/livectl")
python  := env("PYTHON", ".venv/bin/python")
pytest  := env("PYTEST", ".venv/bin/pytest")
```

`test-py` and `test-ui` call `{{pytest}}` instead of `{{venv}}/bin/pytest`.
- [ ] **Step 5: Verify.** `just test` → PASS. `LIVECTL=livectl PYTEST=pytest just --evaluate livectl` prints `livectl`; `just --evaluate pytest` prints `.venv/bin/pytest`.
- [ ] **Step 6: Commit** `fix: find the test source script from the working directory; let just use an installed livectl`

---

### Task 2: Named access denials on nodes

**Files:**
- Modify: `tools/livectl/pipeline.py` (`_read`, new constants and function)
- Test: `tools/tests/test_pipeline.py`

**Interfaces:**
- Produces: `denial(error: BaseException, service: str) -> Optional[str]`; `SERVICE_FOR_KEY: dict[str, str]`.

- [ ] **Step 1: Write the failing tests**

```python
from botocore.exceptions import ClientError

def client_error(code, operation):
    return ClientError({"Error": {"Code": code, "Message": "User: arn:... is not authorized"}}, operation)

def test_an_access_denied_names_the_iam_action():
    assert denial(client_error("AccessDeniedException", "DescribeChannel"), "medialive") == \
        "access denied: medialive:DescribeChannel"
    assert denial(client_error("AccessDenied", "GetDistribution"), "cloudfront") == \
        "access denied: cloudfront:GetDistribution"

def test_other_errors_are_not_denials():
    assert denial(client_error("ThrottlingException", "DescribeChannel"), "medialive") is None
    assert denial(RuntimeError("AccessDeniedException: stubbed failure"), "medialive") is None

def test_a_denied_channel_read_shows_the_action_on_the_node():
    # stub_aws with describe_channel raising client_error("AccessDeniedException", "DescribeChannel")
    # -> the medialive_channel node's error == "access denied: medialive:DescribeChannel"
```

For the third test, build the clients with `stub_aws()` and replace `clients["medialive"]` with a `StubClient` whose `describe_channel` is the `ClientError` (other operations as in `stub_aws`).
- [ ] **Step 2: Run** `.venv/bin/pytest -q tools/tests/test_pipeline.py` → FAIL (`denial` not defined).
- [ ] **Step 3: Implement.** `DENIED_CODES = {"AccessDenied", "AccessDeniedException", "UnauthorizedOperation"}`. `SERVICE_FOR_KEY = {"metrics": "cloudwatch", "flow": "mediaconnect", "source-events": "logs", "input": "medialive", "channel": "medialive", "alerts": "medialive", "package": "mediapackagev2", "cdn": "cloudfront"}` (IAM action prefixes). `denial` checks `isinstance(error, ClientError)`, the code in `error.response["Error"]["Code"]`, and uses `error.operation_name`. In `_read`, return `denial(...)` when the key has a service and it is not None, else the current `f"{type(error).__name__}: {error}"`. Import `ClientError` from `botocore.exceptions` (botocore comes with boto3).
- [ ] **Step 4: Run** `just test` → PASS (the `probe-error` scenario still shows its RuntimeError text).
- [ ] **Step 5: Commit** `feat: name the denied IAM action on a node instead of the raw error`

---

### Task 3: Binding inside a container

**Files:**
- Modify: `tools/livectl/server.py` (`make_server`, `serve`), `tools/livectl/cli.py` (`ui` parser, `_loopback` removed)
- Test: `tools/tests/test_server.py`, `tools/tests/test_cli.py`

**Interfaces:**
- Produces: `CONTAINER_HOST = "0.0.0.0"`; `make_server(console, *, host="127.0.0.1", port=8765, container: bool = False)`; `serve(console, *, host, port, open_browser=True, container=False, url: Optional[str] = None)`. `livectl ui --container`.

- [ ] **Step 1: Write the failing tests** in `test_server.py` (next to `test_make_server_refuses_a_network_interface`, which stays):

```python
def test_a_container_may_bind_every_interface(aws):
    server = make_server(make_console(), host="0.0.0.0", port=0, container=True)
    server.server_close()

def test_a_container_still_refuses_a_specific_network_address(aws):
    with pytest.raises(ConsoleError):
        make_server(make_console(), host="192.168.1.5", port=0, container=True)
```

and in `test_cli.py`:

```python
def test_ui_refuses_every_interface_without_container(capsys):
    with pytest.raises(SystemExit) as exit_:
        main(["ui", "--host", "0.0.0.0", "--no-browser"])
    assert exit_.value.code == 2 and "loopback" in capsys.readouterr().err

def test_ui_in_a_container_announces_the_host_url(monkeypatch, capsys):
    # monkeypatch livectl.cli.serve with a fake recording its kwargs; env CONSOLE_URL=http://127.0.0.1:9000/
    main(["ui", "--host", "0.0.0.0", "--container", "--no-browser"])
    # the fake got container=True and url="http://127.0.0.1:9000/"
```

- [ ] **Step 2: Run** → FAIL (`container` not accepted).
- [ ] **Step 3: Implement.** In `make_server`, allowed hosts are `LOOPBACK | {CONTAINER_HOST}` when `container` else `LOOPBACK`; the refusal message is unchanged for the loopback case. `serve` prints and opens `url` when given, else the computed one. In `cli.py`, `--host` becomes a plain string; after parsing, `ui` calls `parser.error(...)` (exit 2) when the host is not allowed for the given `--container`. Add `--container` (help: "running in a container whose published port is bound to the host's loopback; allows --host 0.0.0.0"). `cli` passes `url=os.environ.get("CONSOLE_URL") if args.container else None`. Module docstring of `server.py`: add one sentence on the container case and where the loopback guarantee moves.
- [ ] **Step 4: Run** `just test` → PASS.
- [ ] **Step 5: Commit** `feat: let the console listen on every interface inside a container, and only there`

---

### Task 4: `identity.py`

**Files:**
- Create: `tools/livectl/identity.py`, `tools/tests/test_identity.py`
- Modify: `tools/tests/stubs.py` (add `sts_client`)

**Interfaces:**
- Produces:

```python
IDENTITY_TTL = 15.0
USABLE = {"ok", "root", "unverified"}

@dataclass(frozen=True)
class Identity:
    kind: str                     # ok | root | unverified | no-credentials | expired | invalid
    region: str
    account: Optional[str] = None
    arn: Optional[str] = None
    message: Optional[str] = None # banner text; None for ok and unverified
    @property
    def usable(self) -> bool: ...
    @property
    def label(self) -> str: ...   # header / CLI line
    @property
    def verdict(self) -> str: ... # verdict text when not usable
    def to_dict(self) -> dict: ...# asdict plus "usable", "label"

def whoami(sts, region: str, profile: Optional[str] = None) -> Identity: ...
```

- `stubs.sts_client(kind: str = "ok") -> StubClient`: `ok` answers `arn:aws:sts::123456789012:assumed-role/LiveOps/william`; `root` answers `arn:aws:iam::123456789012:root`; `no-credentials` raises `botocore.exceptions.NoCredentialsError()`; `expired` raises `ClientError` code `ExpiredToken`; `invalid` raises `ClientError` code `InvalidClientTokenId`.

- [ ] **Step 1: Write the failing tests** in `test_identity.py`, one per row:

| Given | kind | label / message / verdict |
|---|---|---|
| moto (`aws` fixture) | `ok` | label `acting as user moto · account 123456789012 · us-east-1` |
| `sts_client("ok")` | `ok` | label `acting as role LiveOps (william) · account 123456789012 · us-east-1` |
| `sts_client("root")` | `root` | label `acting as root · account 123456789012 · us-east-1`; message starts `You are using the account's root user` |
| `sts_client("no-credentials")` | `no-credentials` | verdict `No AWS credentials`; message contains `aws configure` |
| `sts_client("expired")`, profile `demo` | `expired` | verdict `AWS session expired`; message contains `aws sso login --profile demo` |
| `sts_client("expired")`, profile `None` | `expired` | message contains `aws sso login` and not `--profile` |
| `sts_client("invalid")` | `invalid` | verdict `AWS credentials rejected` |
| stub raising `EndpointConnectionError(endpoint_url="https://sts")` | `unverified` | usable; label `identity not verified · us-east-1`; message `None` |

Also: `expired` covers the codes `ExpiredToken`, `ExpiredTokenException`, and the exceptions `TokenRetrievalError`, `UnauthorizedSSOTokenError`, `SSOTokenLoadError`; `invalid` covers `InvalidClientTokenId`, `SignatureDoesNotMatch`, `UnrecognizedClientException` (parametrize).

- [ ] **Step 2: Run** `.venv/bin/pytest -q tools/tests/test_identity.py` → FAIL (module missing).
- [ ] **Step 3: Implement `identity.py`** with a module docstring (why STS: it needs no permission, so it separates credential problems from permission problems). `whoami` never raises. Name from the ARN: `...:root` → `root`; `:user/<path>/<name>` → `user <name>`; `:assumed-role/<role>/<session>` → `role <role> (<session>)`; anything else → the ARN. Messages, exactly:
  - root: `You are using the account's root user. Create an IAM user or role for this project: root cannot be restricted by any policy.`
  - no-credentials: `No AWS credentials found. Configure a profile on the host (aws configure, or aws configure sso), set AWS_PROFILE, then reload.`
  - expired: `Your AWS session has expired. Run "aws sso login{ --profile P}" on the host; the console picks it up within 15 seconds.`
  - invalid: `AWS rejected these credentials: the access key is wrong or has been deactivated.`
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat: classify the AWS identity the tool acts as`

---

### Task 5: The console and the CLI use the identity

**Files:**
- Modify: `tools/livectl/actions.py` (`Situation`, `refusals`), `tools/livectl/server.py` (`Console`, `_snapshot`, `_pipeline_payload`), `tools/livectl/cli.py`
- Test: `tools/tests/test_actions.py`, `tools/tests/test_server.py`, `tools/tests/test_cli.py`

**Interfaces:**
- Consumes: `whoami`, `Identity`, `IDENTITY_TTL` (Task 4); `stubs.sts_client` (Task 4).
- Produces: `Situation.credentials: Optional[str] = None` (the identity's message when unusable). `Console.sts: Any = None`, `Console.profile: Optional[str] = None`, `Console.clock: Callable[[], float] = time.monotonic`, `Console.identity() -> Optional[Identity]` (None when `sts` is None; cached `IDENTITY_TTL` in a `TtlCache` built from `clock`). `/api/pipeline` gains `identity: dict | None`.

- [ ] **Step 1: Write the failing tests**

`test_actions.py`:
```python
def test_without_credentials_every_aws_action_is_refused_with_the_reason():
    s = Situation(deployed=False, job_running=None, flow_state=None, channel_state=None, credentials="No AWS credentials found. …")
    r = refusals(s)
    assert all(r[a] and r[a].status == 403 and r[a].message == s.credentials for a in ACTIONS if a != "source-stop")
    assert r["source-stop"] is None
```

`test_server.py`:
- `test_the_pipeline_payload_carries_the_identity`: console with `sts=sts_client("ok")` → `payload["identity"]["kind"] == "ok"` and `label` as in Task 4.
- `test_without_credentials_the_chain_is_not_polled`: `sts=sts_client("no-credentials")`, `runner` that records calls → payload `verdict == {"text": "No AWS credentials", "health": "bad"}`, `nodes == []`, `deployed is False`, every action except `source-stop` refused, and the runner (terraform output) was never called.
- `test_an_expired_session_recovers_after_login_without_a_restart` (Review Focus 2): a stub STS that raises `ExpiredToken` until a flag flips, a fake clock; first payload is `expired`; flip the flag; advance the clock by `IDENTITY_TTL`; next payload is `ok`.
- `test_a_console_without_sts_behaves_as_before`: `payload["identity"] is None` and the existing payload is unchanged.
- `test_post_deploy_is_refused_without_credentials`: `POST /api/deploy` → 403 with the identity message.

`test_cli.py`:
- `test_status_prints_the_identity_first(aws, capsys)`: first line of stdout starts `acting as user moto`.
- `test_check_clean_without_credentials_exits_2`: monkeypatch `boto3.Session` to one whose `client("sts")` returns `sts_client("no-credentials")` → exit 2, message on stderr.
- `test_a_missing_profile_is_one_clear_line` (Review Focus 1): `monkeypatch.setenv("AWS_PROFILE", "nope")`, for each of `["status", ...flags]`, `["check-clean"]`, `["ui", "--no-browser"]` (with `serve` faked): exit 2, stderr is one line containing `nope` and `~/.aws/config`, no traceback.

- [ ] **Step 2: Run** `just test-py` → FAIL. Existing `test_cli.py` tests that reach `status` or `check-clean` without the `aws` fixture now call STS; give them the fixture so they never reach AWS.
- [ ] **Step 3: Implement.**
  - `refusals`: `no_credentials = Refusal(403, s.credentials) if s.credentials else None`, placed first in every action's `_first(...)` except `source-stop`.
  - `_snapshot`: call `console.identity()` first. When it exists and is not usable, return the not-deployed-shaped payload with `note=None`, `verdict={"text": identity.verdict, "health": "bad"}` and `Situation(..., credentials=identity.message)`, without calling `console.targets()` (terraform output reads the S3 state, which also needs credentials).
  - `_pipeline_payload`: `payload["identity"] = identity.to_dict() if identity else None`.
  - `cli.py`: build `sts = session.client("sts")` once. `status` and `check-clean` print `identity.label` first and return 2 with `error: <message>` on stderr when unusable. `ui` passes `sts=client("sts")` and `profile=session.profile_name` to `Console`. Add `botocore.exceptions.ProfileNotFound` to the handled errors in `main`, printing `error: AWS profile '<name>' is not in ~/.aws/config. Set AWS_PROFILE to one that is, or create it: aws configure --profile <name>` and returning 2. `start`/`stop` are unchanged.
- [ ] **Step 4: Run** `just test` → PASS.
- [ ] **Step 5: Commit** `feat: show who the console acts as, and refuse AWS actions without usable credentials`

---

### Task 6: Identity on the page

**Files:**
- Modify: `tools/livectl/site/index.html`, `tools/livectl/site/app.js`, `tools/livectl/site/app.css`, `tools/tests/scenarios.py`, `tools/tests/test_ui.py`

**Interfaces:**
- Consumes: `payload.identity` (Task 5), `stubs.sts_client` (Task 4).
- Produces: elements `#identity` (header chip) and `#identity-banner` (`role="alert"`, separate from `#banner`, which stays the lost-contact banner). Scenario option `identity: str` (default `"ok"`), new scenarios `no-credentials` and `root-identity`.

- [ ] **Step 1: Scenarios.** `build()` pops `identity` (default `"ok"`) and passes `sts=sts_client(identity)` to `Console`. Add `"no-credentials": dict(identity="no-credentials")` and `"root-identity": dict(identity="root")`.
- [ ] **Step 2: Write the failing browser tests** in `test_ui.py`. Add to `EXPECTED`: `"no-credentials": ("No AWS credentials", [])`, `"root-identity": ("Off air", ["deploy", "teardown", "scan", "go-live"])`. New tests:
  - `test_the_header_says_who_the_console_acts_as`: `on-air-playing` → `#identity` has text `acting as role LiveOps (william) · account 123456789012 · us-east-1`; `#identity-banner` hidden.
  - `test_root_credentials_raise_a_red_banner`: `root-identity` → `#identity-banner` visible, text starts `You are using the account's root user`, `data-kind="root"`.
  - `test_no_credentials_shows_one_banner_and_no_chain`: `no-credentials` → banner contains `aws configure`; `.chain-card` shows no nodes; `#identity` hidden.
- [ ] **Step 3: Run** `just test-ui` → the new tests FAIL.
- [ ] **Step 4: Implement.** `index.html`: `<span class="chip muted" id="identity" hidden></span>` before `#cost`; `<div class="banner" id="identity-banner" role="alert" hidden></div>` after `#banner`. `app.js`: a `renderIdentity(identity)` called from the poll handler, re-rendering only when `changed('identity', …)`: the chip shows `label` when usable; the banner shows `message` when set, with `data-kind` = `kind`. `app.css`: `#identity-banner[data-kind="root"]` uses the existing bad/red token; other kinds use the existing banner style. `node-detail` needs no change: the denial text arrives in `error`.
- [ ] **Step 5: Run** `just test` (with Playwright) → PASS. `just ui-scenario root-identity` and `just ui-scenario no-credentials`, and look at both by hand.
- [ ] **Step 6: Commit** `feat: show the AWS identity in the console header, with banners for root and missing credentials`

---

### Task 7: Dockerfile and Compose

**Files:**
- Create: `docker/Dockerfile`, `.dockerignore`, `compose.yaml`, `.env.example`
- Modify: `.gitignore` (add `.terraform-docker/`)

**Interfaces:**
- Produces: image targets `runtime` and `test`; Compose service `console`; env names `CONSOLE_PORT`, `AWS_PROFILE`, `UID`, `GID`, `CONSOLE_URL`.

- [ ] **Step 1: Dockerfile.**

```dockerfile
# syntax=docker/dockerfile:1
FROM hashicorp/terraform:1.16 AS terraform

FROM python:3.12-alpine AS runtime
# bash: the justfile and source/send-srt.sh need it. font-dejavu: the burned-in clock.
RUN apk add --no-cache bash ffmpeg font-dejavu just
COPY --from=terraform /bin/terraform /usr/local/bin/terraform
COPY tools /tmp/tools
RUN pip install --no-cache-dir /tmp/tools && rm -rf /tmp/tools
# HOME is writable by any UID, because compose runs as the host user's UID (see compose.yaml).
RUN mkdir -p /home/app && chmod 1777 /home/app
ENV HOME=/home/app LIVECTL=livectl PYTHON=python PYTHONDONTWRITEBYTECODE=1 \
    FONT=/usr/share/fonts/dejavu/DejaVuSans.ttf TF_DATA_DIR=.terraform-docker
WORKDIR /work
USER 1000:1000
EXPOSE 8765
CMD ["livectl", "ui", "--host", "0.0.0.0", "--container", "--no-browser"]

FROM runtime AS test
USER root
COPY tools /tmp/tools
RUN pip install --no-cache-dir "/tmp/tools[dev]" && rm -rf /tmp/tools
ENV PYTEST=pytest
USER 1000:1000
```

`TF_DATA_DIR` is in the image, not Compose, so `docker compose run` and CI get it too.
- [ ] **Step 2: `.dockerignore`:** `.git`, `.venv`, `**/.terraform`, `**/.terraform-docker`, `**/__pycache__`, `**/*.egg-info`, `**/*.tfstate*`, `**/*.tfvars`, `**/backend.hcl`, `.env`, `.superpowers`, `.claude`, `.agents`, `docs`. State and secrets must never enter a build context.
- [ ] **Step 3: `compose.yaml`.**

```yaml
services:
  console:
    image: ghcr.io/wbm99/aws-channel-with-terraform:latest
    build: { context: ., dockerfile: docker/Dockerfile, target: runtime }
    # Files written into the checkout (state, provider caches) belong to the host user.
    user: "${UID:-1000}:${GID:-1000}"
    ports:
      # Keep the 127.0.0.1 prefix: without it the console, which can destroy the stack, is reachable from the LAN.
      - "127.0.0.1:${CONSOLE_PORT:-8765}:8765"
    volumes:
      - type: bind
        source: .
        target: /work
      - type: bind
        source: ${HOME}/.aws
        target: /home/app/.aws
        read_only: true
        bind: { create_host_path: false }  # a missing ~/.aws is an error, not a new root-owned directory
    environment:
      AWS_PROFILE: ${AWS_PROFILE:-default}
      CONSOLE_URL: http://127.0.0.1:${CONSOLE_PORT:-8765}/
```

`AWS_PROFILE` defaults to `default` rather than an empty string, which botocore would treat as a profile named "".
- [ ] **Step 4: `.env.example`**, each line commented: `CONSOLE_PORT=8765`, `AWS_PROFILE=default`, `UID=1000` / `GID=1000` (with the hint `id -u` / `id -g`: set them when yours are not 1000). Add `.terraform-docker/` to `.gitignore`.
- [ ] **Step 5: Verify the image.**
  - `docker compose build` → succeeds. `docker image ls` shows the size; record it for the README.
  - `docker compose run --rm console sh -c 'terraform version && ffmpeg -hide_banner -protocols | grep -qw srt && just --version && livectl --help >/dev/null && echo ok'` → `ok`.
  - `docker compose run --rm console source/send-srt.sh --frame /tmp/f.png` → exit 0.
  - `docker compose run --rm console just test-tf` → PASS (runs as the host UID; `.terraform-docker/` appears in the modules and is owned by the host user: `stat -c %U modules/ingest/.terraform-docker`).
  - The `test` target (built with plain `docker build`, since Compose builds only `runtime`): `docker build -f docker/Dockerfile --target test -t livectl-test . && docker run --rm -u "$(id -u):$(id -g)" -v "$PWD:/work" livectl-test just test-py` → PASS.
  - Review Focus 3: `HOME=/tmp/nohome docker compose config` succeeds, but `HOME=/tmp/nohome docker compose up` fails with an error naming the missing path, and `/tmp/nohome/.aws` was not created.
  - Review Focus 5: `CONSOLE_PORT=9000 docker compose up -d`, then `docker compose logs console` shows `http://127.0.0.1:9000/` and `curl -sf http://127.0.0.1:9000/ >/dev/null` succeeds; `curl -sf http://<host LAN IP>:9000/` fails. Then `docker compose down`.
  - `AWS_PROFILE` from `.env`: with `AWS_PROFILE=demo` in a scratch `.env`, `docker compose config` shows `AWS_PROFILE: demo`. Remove the scratch `.env`.
- [ ] **Step 6: Commit** `feat: a Docker image and compose file that run the whole project`

---

### Task 8: CI workflow

**Files:**
- Create: `.github/workflows/image.yml`

**Interfaces:**
- Consumes: image targets (Task 7), justfile variables (Task 1).

- [ ] **Step 1: Write the workflow.** Triggers: `pull_request`, and `push` to `main`. `permissions: contents: read` at the top; `packages: write` on `publish` only. Jobs:
  - `test` (ubuntu-latest): checkout; `docker/setup-buildx-action`; `docker/build-push-action` with `target: test`, `load: true`, `tags: livectl-test`, `cache-from/cache-to: type=gha`; then `docker run --rm -u "$(id -u):$(id -g)" -v "$PWD:/work" livectl-test just test-tf test-py`.
  - `ui` (ubuntu-latest): checkout; `actions/setup-python` 3.12; `extractions/setup-just`; `just venv`; `.venv/bin/pip install -e "tools[dev,ui]"`; `google-chrome --version` (the runner image ships Chrome, which `test_ui.py` launches with `channel="chrome"`); `just test-ui`.
  - `publish` (`if: github.event_name == 'push' && github.ref == 'refs/heads/main'`, `needs: [test, ui]`): checkout; `docker/setup-qemu-action`; buildx; `docker/login-action` to `ghcr.io` with `github.actor` / `secrets.GITHUB_TOKEN`; build `runtime` for `linux/amd64` with `load: true` and run the smoke check (Step 2); then `docker/build-push-action` with `platforms: linux/amd64,linux/arm64`, `push: true`, tags `ghcr.io/${{ github.repository }}:latest` and `ghcr.io/${{ github.repository }}:sha-${{ github.sha }}` shortened to 7 characters (compute it in a prior step), gha cache.
- [ ] **Step 2: Smoke check** (a `run:` step in `publish`), with the checkout mounted at `/work` and `-u "$(id -u):$(id -g)"`: `terraform version`; `ffmpeg -hide_banner -protocols | grep -qw srt`; `source/send-srt.sh --frame /tmp/f.png`; `livectl --help`; then start `livectl ui --host 0.0.0.0 --container --no-browser` detached with `-p 127.0.0.1:8765:8765`, poll `curl -sf http://127.0.0.1:8765/` for up to 20 s, and stop the container. Review Focus 4: `touch /work/.owner-check` inside, then `test "$(stat -c %u .owner-check)" = "$(id -u)"` on the runner, then remove it.
- [ ] **Step 3: Validate locally.** `docker run --rm -v "$PWD:/repo" rhysd/actionlint:latest -color /repo/.github/workflows/image.yml` → no findings. Run the smoke-check commands from Step 2 against the local image by hand → all succeed.
- [ ] **Step 4: Commit** `feat: test every pull request in the image and publish it to GHCR on merge to main`

The workflow's first real run happens when the user pushes the branch and opens a PR; `publish` runs only after merge. Report that it has not run yet.

---

### Task 9: Documentation

**Files:**
- Modify: `README.md`, `AGENTS.md`

- [ ] **Step 1: README, new section *Run with Docker*** after *Prerequisites*, before *Quick start* (which stays as the host path):
  - Needs: Docker with Compose v2, and an AWS profile in `~/.aws`. Without the AWS CLI: `docker run --rm -it -v ~/.aws:/root/.aws amazon/aws-cli configure sso` (or `configure`). Never the root user.
  - The four commands from the spec, then "everything else is in the console".
  - Any recipe: `docker compose run --rm console just <recipe>`.
  - `.env` (copy `.env.example`): `CONSOLE_PORT`, `AWS_PROFILE`, `UID`/`GID`.
  - A warning box: `docker compose down` stops the console only, not AWS billing. Go off air or Tear down stack first, then Scan for leftovers.
  - Use `docker compose up --build` on a checkout other than current `main` (livectl comes from the image, Terraform code from the checkout).
  - The image size recorded in Task 7.
  - *Verify the console live* checklist: add steps for `just bootstrap` / `just init` through `docker compose run`, and one Deploy → Go live → Send test source → Tear down → Scan for leftovers cycle from the containerised console with an SSO profile.
  - Prerequisites: one line pointing to the Docker section as the alternative to installing everything.
- [ ] **Step 2: AGENTS.md.** Under *Python conventions*: the `--container` rule and why the Compose port line keeps `127.0.0.1:`; identity is checked through injected STS. New short section *Containers*: one image, `runtime` has no dev dependencies, `TF_DATA_DIR=.terraform-docker`, the checkout is bind-mounted, CI in `.github/workflows/image.yml` runs `just test-tf test-py` in the `test` target and Playwright on the runner, and publishes on merge to `main`. Under *Commands*: `docker compose up` as the Docker entry point.
- [ ] **Step 3: Verify.** `just test` → PASS; every command in the new README section has been run in Task 7 or 8 except the four live ones (`bootstrap`, `init`, and console actions), which cost money and are the user's to run.
- [ ] **Step 4: Commit** `docs: running the project with Docker`

---

## After the plan

Not verified offline, and the user's to run: `docker compose run --rm console just bootstrap` / `just init` and a Deploy → Go live → Send test source → Tear down → Scan for leftovers cycle from the containerised console on a real account, with an SSO profile. They are added to the README's *Verify the console live* checklist in Task 9, Step 1.
