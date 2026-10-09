# Live SRT to HLS on AWS.
# `just --list` shows every recipe; this file is the source of truth for the underlying commands.
# Resources here bill by the hour while running -- see the money rules in AGENTS.md.

set shell := ["bash", "-uc"]

tf_dir  := "envs/demo"
venv    := ".venv"
# The Docker image sets these to its installed tools; on the host they default to the virtualenv.
livectl := env("LIVECTL", ".venv/bin/livectl")
python  := env("LIVECTL_PYTHON", ".venv/bin/python")
pytest  := env("LIVECTL_PYTEST", ".venv/bin/pytest")
# Tests point this at a fake; nothing else needs to.
docker  := env("LIVECTL_DOCKER", "docker")

# Show the available recipes
default:
    @just --list --unsorted

# --- setup -------------------------------------------------------------------

# Create .venv and install livectl with its dev extras
venv:
    #!/usr/bin/env bash
    set -euo pipefail
    if [[ ! -x {{venv}}/bin/python ]]; then
      python3 -m virtualenv {{venv}} 2>/dev/null || python3 -m venv {{venv}}
    fi
    {{venv}}/bin/pip install --quiet --upgrade pip
    {{venv}}/bin/pip install --quiet -e "tools[dev]"
    echo "ready: {{livectl}}"

# One-time: create the state bucket and the monthly budget (this root is never destroyed)
bootstrap:
    #!/usr/bin/env bash
    set -euo pipefail
    terraform -chdir=bootstrap init -input=false
    terraform -chdir=bootstrap apply -input=false
    echo
    echo "state bucket: $(terraform -chdir=bootstrap output -raw state_bucket_name)"
    echo "put that name in {{tf_dir}}/backend.hcl, then run: just init"

# Initialise the demo environment against the remote state bucket
init:
    terraform -chdir={{tf_dir}} init -backend-config=backend.hcl -input=false

# --- stack -------------------------------------------------------------------

# Show what applying the demo stack would change
plan:
    terraform -chdir={{tf_dir}} plan -input=false

# Deploy stack: create or update the demo stack (a few minutes, CloudFront included)
up:
    #!/usr/bin/env bash
    set -euo pipefail
    terraform -chdir={{tf_dir}} apply -input=false
    echo
    echo "player: $(terraform -chdir={{tf_dir}} output -raw player_url)"

# Tear down stack: destroy the demo stack, then verify nothing billable survived
down:
    #!/usr/bin/env bash
    set -euo pipefail
    terraform -chdir={{tf_dir}} destroy -input=false
    just check-clean

# --- operating ---------------------------------------------------------------

# Go live: start the flow, then the channel (about 2 minutes; billing starts here)
start:
    {{livectl}} start

# Go off air: stop the channel, then the flow
stop:
    {{livectl}} stop

# Show flow, channel and source state
status:
    {{livectl}} status

# Scan for leftovers: fail if any billable resource was left behind
check-clean:
    {{livectl}} check-clean

# Serve the control center on http://127.0.0.1:8765, with Grafana beside it when Docker is available
ui port="8765":
    #!/usr/bin/env bash
    set -uo pipefail
    # A Grafana already running (from `docker compose up`) is left running when the console exits.
    running="$(command -v {{docker}} >/dev/null 2>&1 && {{docker}} compose ps -q --status running grafana 2>/dev/null)"
    url="$({{just_executable()}} grafana-up | tail -n 1)"
    echo "$url"
    flags=()
    if [[ "$url" == http* ]]; then
      flags=(--grafana-url "$url")
      [[ -n "$running" ]] || trap '{{just_executable()}} grafana-down' EXIT
    fi
    # ${flags[@]+...}: bash before 4.4 (macOS's /bin/bash) calls an empty array unbound under `set -u`.
    {{livectl}} ui --port {{port}} ${flags[@]+"${flags[@]}"}

# Start Grafana (docker compose) and print its dashboard URL; says why and succeeds when it cannot
grafana-up:
    #!/usr/bin/env bash
    set -uo pipefail
    if ! command -v {{docker}} >/dev/null 2>&1; then
      echo "Grafana skipped: docker not found"; exit 0
    fi
    echo "starting Grafana (the first run pulls its image)..." >&2
    if ! error="$({{docker}} compose up -d grafana 2>&1 >/dev/null)"; then
      echo "Grafana skipped: $(printf '%s\n' "$error" | tail -n 1)"; exit 0
    fi
    address="$({{docker}} compose port grafana 3000 2>/dev/null | tail -n 1)"
    if [[ -z "$address" ]]; then
      echo "Grafana skipped: Docker published no port for it"; exit 0
    fi
    echo "http://${address}/d/mediaconnect-source"

# Stop Grafana; does nothing without Docker
grafana-down:
    @command -v {{docker}} >/dev/null 2>&1 && {{docker}} compose stop grafana >/dev/null 2>&1 || true

# --- source ------------------------------------------------------------------

# Push the FFmpeg test stream to the ingest (leave running; Ctrl-C stops it)
send:
    #!/usr/bin/env bash
    set -euo pipefail
    SRT_HOST="$(terraform -chdir={{tf_dir}} output -raw ingest_ip)"
    SRT_PORT="$(terraform -chdir={{tf_dir}} output -raw ingest_port)"
    # Resolved at the moment of use so the passphrase never lands in a file or the shell history.
    SRT_PASSPHRASE="$(aws secretsmanager get-secret-value \
      --secret-id "$(terraform -chdir={{tf_dir}} output -raw passphrase_secret_arn)" \
      --query SecretString --output text)"
    export SRT_HOST SRT_PORT SRT_PASSPHRASE
    echo "sending to srt://${SRT_HOST}:${SRT_PORT}"
    exec source/send-srt.sh

# Render one frame locally to check the burned-in clock (no network, no AWS)
frame out="/tmp/clock.png":
    source/send-srt.sh --frame {{out}}

# --- information -------------------------------------------------------------

# Print every Terraform output of the demo environment
outputs:
    terraform -chdir={{tf_dir}} output

# Print the player URL
player:
    @terraform -chdir={{tf_dir}} output -raw player_url && echo

# Print the SRT ingest endpoint
ingest:
    @echo "srt://$(terraform -chdir={{tf_dir}} output -raw ingest_ip):$(terraform -chdir={{tf_dir}} output -raw ingest_port)"

# --- quality -----------------------------------------------------------------

# Format every Terraform file in place
fmt:
    terraform fmt -recursive

# Check formatting and validate every Terraform root
validate:
    #!/usr/bin/env bash
    set -euo pipefail
    terraform fmt -recursive -check -diff
    for dir in bootstrap modules/*/; do
      dir="${dir%/}"
      terraform -chdir="$dir" init -backend=false -input=false >/dev/null
      terraform -chdir="$dir" validate
    done

# Run every offline test: Terraform with mocked providers, then Python with moto
test: test-tf test-py

# terraform test for bootstrap and every module (no AWS credentials needed)
test-tf:
    #!/usr/bin/env bash
    set -euo pipefail
    for dir in bootstrap modules/*/; do
      dir="${dir%/}"
      echo "== $dir"
      terraform -chdir="$dir" init -backend=false -input=false >/dev/null
      terraform -chdir="$dir" test
    done

# pytest over the livectl package (no AWS credentials needed)
test-py:
    {{pytest}} -q tools

# Serve the console in a named fake state, with no AWS (see tools/tests/scenarios.py)
ui-scenario name="on-air-playing" port="8766":
    {{python}} tools/tests/scenarios.py {{name}} {{port}}

# Start the real Grafana service with no AWS credentials and check it provisions (needs Docker; pulls the image once)
test-grafana:
    {{pytest}} -q tools/tests/test_grafana_container.py

# Drive the console in Chrome through every scenario (needs: .venv/bin/pip install -e "tools[dev,ui]")
test-ui:
    {{pytest}} -q tools/tests/test_ui.py

# Look up on-demand prices behind docs/cost-estimate.md
cost service="AWSElementalMediaLive" pattern="Single Pipeline (HD|SD) AVC":
    {{python}} scripts/price_lookup.py {{quote(service)}} {{quote(pattern)}}
