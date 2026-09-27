# Control Center Frontend Implementation Plan (Plan 6 of 7)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the console page with the control center: verdict and cost in the header, the seven-node chain, the player next to the selected node's details, controls grouped and named by their effect, endpoints that copy exactly their value, and a collapsible log panel (Jobs tab only until Plan 7). Every state the page can be in is driven in a real browser by a test.

**Architecture:** Native ES modules under `tools/livectl/site/`, no build step, no dependencies. The page polls `GET /api/pipeline` every 2 s and re-renders a region only when its data changed, so focus and a half-typed confirmation survive polls. All data goes into the DOM with `textContent`. The old routes and the old page are deleted. Scenario fixtures (`tools/tests/scenarios.py`) run the real server with stub clients in named states; Playwright for Python (dev extra only) drives the installed Chrome through each one.

**Tech Stack:** HTML, CSS, JavaScript modules; Python stdlib `http.server`; pytest; Playwright for Python with `channel="chrome"`.

**Spec:** `docs/superpowers/specs/2026-09-26-control-center-design.md`
**Depends on:** Plan 5.

## Global Constraints

- No framework, no bundler, no npm. The runtime stays boto3-only; Playwright is a dev extra.
- Never `innerHTML` with data. Use the `el()` helper, which sets text only.
- Copy buttons copy a value held in JavaScript, never displayed text.
- Controls are rendered from `payload.actions`: a button exists only when its value is `null`.
- Messages come from the server's action rules or are written here for a person. Never show CLI flags.
- Keep the existing design tokens and the bundled Inter font.
- The UI tests skip, not fail, when Playwright is not installed; `just test-ui` runs them.
- Every task ends with `just test` green and a commit.

## File Structure

```
tools/livectl/site/index.html        (rewrite)
tools/livectl/site/app.css           (create; styles move out of index.html)
tools/livectl/site/app.js            (create)
tools/livectl/site/api.js            (create)
tools/livectl/site/format.js         (create)
tools/livectl/site/chain.js          (create)
tools/livectl/site/node-detail.js    (create)
tools/livectl/site/controls.js       (create)
tools/livectl/site/endpoints.js      (create)
tools/livectl/site/logs-panel.js     (create)
tools/livectl/server.py              (modify: static files, remove old routes, make_server)
tools/pyproject.toml                 (modify: package-data, ui extra)
tools/tests/scenarios.py             (create)
tools/tests/test_server.py           (modify: drop old-route tests)
tools/tests/test_ui.py               (create)
justfile                             (modify: test-ui, ui-scenario)
```

---

### Task 1: Serve scripts and styles, drop the old routes

**Files:**
- Modify: `tools/livectl/server.py`, `tools/pyproject.toml`, `tools/tests/test_server.py`

**Interfaces:**
- Produces: `GET /<name>.js` and `GET /<name>.css` served from `site/`; `make_server(console, host="127.0.0.1", port=8765) -> _Server` (loopback check included), used by `serve` and by the UI tests.
- Removes: `/api/status`, `/api/start`, `/api/stop`, `/api/apply`, `/api/destroy`, `/api/check-clean`, `_status_payload`, `_forgetting_targets`.

- [ ] **Step 1: Write the failing tests**

In `tools/tests/test_server.py`:
- Delete the tests that call the removed routes: `test_status_says_not_deployed_instead_of_failing`, `test_status_reports_flow_channel_and_endpoints`, `test_start_brings_the_pipeline_up_and_reports_it`, `test_stop_brings_the_pipeline_back_down`, `test_start_without_a_deployed_stack_is_a_readable_400`, `test_stop_without_a_deployed_stack_is_a_readable_400`, `test_apply_runs_terraform_with_auto_approve_and_no_prompts`, `test_destroy_is_refused_without_the_confirmation_token`, `test_destroy_is_refused_when_the_token_is_wrong`, `test_destroy_runs_once_the_token_matches`. Plan 5 added equivalents for each under the new names.
- Rename the routes in the tests that remain: `test_check_clean_needs_no_stack_so_it_still_runs_after_a_destroy`, `test_check_clean_fails_while_a_billable_resource_exists` and `test_check_clean_passes_when_nothing_is_left` call `/api/scan`; `test_a_second_job_is_refused_with_409` calls `/api/go-live`; `test_outputs_are_cached_between_polls_but_re_read_after_terraform` polls `/api/pipeline` and posts `/api/deploy`; `make_handler` uses `/api/pipeline`.
- Add:
```python
def test_scripts_and_styles_are_served_with_their_types(aws):
    for name, kind in (("app.js", "text/javascript"), ("app.css", "text/css")):
        status, content_type, _ = route("GET", "/" + name, {}, {}, make_console())
        assert status == 200 and content_type.startswith(kind), name


def test_the_old_routes_are_gone(aws):
    for path in ("/api/start", "/api/stop", "/api/apply", "/api/check-clean"):
        assert call(make_console(), "POST", path)[0] == 404, path
    assert call(make_console(), "GET", "/api/status")[0] == 404


def test_make_server_refuses_a_network_interface(aws):
    import pytest
    from livectl.server import ConsoleError, make_server

    with pytest.raises(ConsoleError):
        make_server(make_console(), host="0.0.0.0", port=0)
```
Run: `.venv/bin/pytest -q tools/tests/test_server.py` → the three new tests FAIL.

- [ ] **Step 2: Implement**

In `server.py`:
```python
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
}
SITE_FILE = re.compile(r"/[a-z][a-z-]*\.(js|css)")
```
(`import re`.) In `route`'s GET branch, after the index case:
```python
        if SITE_FILE.fullmatch(path):
            return _static(path.lstrip("/"))
```
Delete `_status_payload`, `_forgetting_targets` and the POST branches for the removed paths. Split `serve`:
```python
def make_server(console: Console, *, host: str = "127.0.0.1", port: int = 8765) -> "_Server":
    """A bound console server. Refuses any address that is not loopback."""
    if host not in LOOPBACK:
        raise ConsoleError(f"the console binds to loopback only, not {host!r} (it can destroy infrastructure)")
    handler = type("ConsoleHandler", (_Handler,), {"console": console})
    try:
        return _Server((host, port), handler)
    except OSError as error:
        raise ConsoleError(f"cannot listen on {host}:{port}: {error.strerror or error}") from error


def serve(console: Console, *, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    """Run the console until interrupted."""
    with make_server(console, host=host, port=port) as httpd:
        url = f"http://{host}:{httpd.server_address[1]}/"
        print(f"livectl console on {url}  (Ctrl-C to stop)")
        if open_browser:
            webbrowser.open(url)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped")
```
In `tools/pyproject.toml`:
```toml
[project.optional-dependencies]
dev = ["pytest>=8", "moto>=5"]
ui = ["playwright>=1.45"]

[tool.setuptools.package-data]
livectl = ["site/*.html", "site/*.js", "site/*.css", "site/fonts/*"]
```
Create placeholders so the static test can pass before Task 3 fills them: `tools/livectl/site/app.js` containing `// replaced in Task 3` and an empty `tools/livectl/site/app.css`.

- [ ] **Step 3: Verify and commit**

Run: `.venv/bin/pytest -q tools` → PASS. The old page is now broken (its routes are gone); Task 3 replaces it in the same plan, so do not stop between tasks 1 and 3.
```bash
git add tools/livectl/server.py tools/livectl/site tools/pyproject.toml tools/tests/test_server.py
git commit -m "feat: serve page modules and retire the old console routes"
```

---

### Task 2: Scenario fixtures

**Files:**
- Create: `tools/tests/scenarios.py`
- Modify: `justfile`

**Interfaces:**
- Consumes: `stub_aws`, `Pipeline`, `Console`, `JobRunner`, `make_server`.
- Produces: `SCENARIOS` (names below); `build(name) -> Scenario` where `Scenario` has `console`, `commands: list` (argv of every Terraform command the console tried to run), `close()`; `python tools/tests/scenarios.py <name> [port]` serves one scenario for a person to look at.

Scenario table (the UI test in Task 5 asserts it):

| Scenario | Verdict | Buttons |
|---|---|---|
| `not-deployed` | Not deployed | Deploy stack, Tear down stack, Scan for leftovers |
| `deploying` | Deploying… | none |
| `off-air` | Off air | Deploy stack, Tear down stack, Scan for leftovers, Go live |
| `going-live` | Going live… | none |
| `on-air-no-source` | On air · no source | Scan for leftovers, Go off air |
| `on-air-playing` | On air · playing | Scan for leftovers, Go off air |
| `degraded` | On air · source degraded | Scan for leftovers, Go off air |
| `partly-on` | Partly on | Scan for leftovers, Go live, Go off air |
| `probe-error` | Unknown | Deploy stack, Tear down stack, Scan for leftovers, Go live, Go off air |

- [ ] **Step 1: Write the fixtures**

`tools/tests/scenarios.py`:
```python
"""Named states of the whole console, for browser tests and for looking at the page by hand.

Each scenario runs the real server, routing, verdict and action rules against stub AWS clients, so the page can be
driven through every state without an AWS account. Run one with:  python tools/tests/scenarios.py on-air-playing
"""

from __future__ import annotations

import json
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from livectl.jobs import JobRunner  # noqa: E402
from livectl.pipeline import Pipeline  # noqa: E402
from livectl.server import Console, make_server  # noqa: E402
from stubs import CHANNEL_ID, FLOW_ARN, stub_aws  # noqa: E402

OUTPUTS = {
    "flow_arn": FLOW_ARN,
    "medialive_channel_id": CHANNEL_ID,
    "medialive_input_id": "4412345",
    "mediapackage_channel_group": "live-sports-aws-demo",
    "mediapackage_channel": "live-sports-aws-demo",
    "mediapackage_endpoint": "live-sports-aws-demo-hls",
    "distribution_id": "E2EXAMPLE",
    "cdn_manifest_url": "https://d1.example.net/out/v1/live/index.m3u8",
    # Port 9 refuses connections at once, so the player frame fails fast and offline.
    "player_url": "http://127.0.0.1:9/",
    "ingest_ip": "203.0.113.20",
    "ingest_port": 5000,
    "passphrase_secret_arn": "arn:aws:secretsmanager:us-east-1:123456789012:secret:live-sports-aws-demo-srt-AbC",
}
LIVE = {"src_connected": 1.0, "src_bitrate": 6_000_000.0, "src_not_recovered": 0.0, "src_cc_errors": 0.0,
        "ml_alerts": 0.0, "ml_fps": 30.0, "mp_ingress_bytes": 74_000_000.0, "cf_requests": 42.0}
ON = dict(flow="ACTIVE", channel="RUNNING")

SCENARIOS: dict[str, dict] = {
    "not-deployed": dict(deployed=False),
    "deploying": dict(deployed=False, job="deploy"),
    "off-air": dict(),
    "going-live": dict(job="go-live"),
    "on-air-no-source": dict(**ON, metrics=dict(LIVE, src_connected=0.0, src_bitrate=None)),
    "on-air-playing": dict(**ON, metrics=LIVE, playing=True),
    "degraded": dict(**ON, metrics=dict(LIVE, src_not_recovered=12.0), playing=True),
    "partly-on": dict(flow="ACTIVE"),
    "probe-error": dict(fail=("describe_flow", "describe_channel")),
}


def growing_playlist():
    state = {"seq": 500}

    def fetch(url: str) -> str:
        if url.endswith("/index.m3u8"):
            return "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=5500000\nindex_1.m3u8\n"
        state["seq"] += 1
        return f"#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:{state['seq']}\n"

    return fetch


def no_playlist(url: str) -> str:
    raise OSError("HTTP Error 404: Not Found")


@dataclass
class Scenario:
    console: Console
    commands: list = field(default_factory=list)
    release: threading.Event = field(default_factory=threading.Event)

    def close(self) -> None:
        self.release.set()


def build(name: str) -> Scenario:
    spec = dict(SCENARIOS[name])
    deployed = spec.pop("deployed", True)
    job = spec.pop("job", None)
    playing = spec.pop("playing", False)
    clients = stub_aws(**spec)
    commands: list = []

    def runner(args):
        return json.dumps({k: {"value": v} for k, v in OUTPUTS.items()} if deployed else {})

    def command(args):
        commands.append(list(args))
        return lambda log: log("$ " + " ".join(args))

    console = Console(**clients, jobs=JobRunner(), runner=runner, command=command,
                      pipeline=Pipeline(**clients, fetch=growing_playlist() if playing else no_playlist))
    scenario = Scenario(console=console, commands=commands)
    if job:
        console.jobs.submit(job, lambda log: (log(f"{job} in progress…"), scenario.release.wait(120)))
    return scenario


def main(argv: list[str]) -> int:
    name = argv[1] if len(argv) > 1 else "on-air-playing"
    port = int(argv[2]) if len(argv) > 2 else 8766
    if name not in SCENARIOS:
        print("scenarios: " + ", ".join(SCENARIOS), file=sys.stderr)
        return 2
    scenario = build(name)
    with make_server(scenario.console, port=port) as httpd:
        print(f"scenario {name} on http://127.0.0.1:{port}/  (Ctrl-C to stop)")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            scenario.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
```
`Console(**clients, ...)` works because the stub dict keys match the `Console` field names.

Add to `justfile` under `# --- quality`:
```
# Serve the console in a named fake state, with no AWS (see tools/tests/scenarios.py)
ui-scenario name="on-air-playing" port="8766":
    {{python}} tools/tests/scenarios.py {{name}} {{port}}

# Drive the console in Chrome through every scenario (needs: .venv/bin/pip install -e "tools[dev,ui]")
test-ui:
    {{venv}}/bin/pytest -q tools/tests/test_ui.py
```

- [ ] **Step 2: Check each scenario's API answer**

Run:
```bash
for s in not-deployed deploying off-air going-live on-air-no-source on-air-playing degraded partly-on probe-error; do
  .venv/bin/python -c "
import sys, json; sys.path.insert(0, 'tools/tests')
from scenarios import build; from livectl.server import route
sc = build('$s'); p = json.loads(route('GET', '/api/pipeline', {}, {}, sc.console)[2])
print('$s'.ljust(18), p['verdict']['text'].ljust(28), sorted(k for k, v in p['actions'].items() if v is None)); sc.close()"
done
```
Expected: the first two columns match the table. `on-air-playing` prints `On air · not reaching viewers` on this single call, because the first look at a playlist cannot tell whether it advances. The browser test waits for the second look.

- [ ] **Step 3: Commit**

```bash
git add tools/tests/scenarios.py justfile
git commit -m "test: add named console scenarios served from stub clients"
```

---

### Task 3: The page

**Files:**
- Rewrite: `tools/livectl/site/index.html`
- Create: `app.css`, `format.js`, `api.js`, `chain.js`, `node-detail.js`, `controls.js`, `endpoints.js`, `logs-panel.js`, `app.js` under `tools/livectl/site/`

**Interfaces (DOM contract used by the UI tests):**
- `#verdict-text` holds the verdict; `#verdict[data-health]` its health.
- `#cost` holds the rate text.
- `#chain button[data-node="<id>"][data-health]`, with `aria-pressed="true"` on the selected node.
- `#detail h2` holds the selected node's title.
- `#controls button[data-action="<name>"]`, one for each action whose value is `null`.
- `#confirm` (hidden until Tear down is clicked), `#confirm-input`, `#confirm-go`.
- `#endpoints button[data-copy="<key>"]` for `ingest`, `player`, `manifest`, `passphrase_secret_arn`.
- `#player-frame` (iframe), `#player-empty` (placeholder).
- `#tabs [role=tab][data-tab="jobs"]`, `#log`.
- `#banner` (role alert), `#panel-toggle`, `#layout.panel-hidden`.

- [ ] **Step 1: `index.html`**

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>livectl</title>
<link rel="stylesheet" href="app.css">
</head>
<body>
<header class="top">
  <h1>live-sports-aws · demo</h1>
  <span class="verdict" id="verdict" data-health="unknown"><i class="dot"></i><span id="verdict-text">connecting…</span></span>
  <span class="spacer"></span>
  <span class="chip" id="cost">$0.00 / h</span>
  <span class="chip muted" id="freshness"></span>
  <button class="chip button" id="panel-toggle" type="button" aria-expanded="true" aria-controls="panel">Logs</button>
</header>
<div class="banner" id="banner" role="alert" hidden></div>
<div class="layout" id="layout">
  <main class="main">
    <section class="card" aria-labelledby="chain-title">
      <h2 class="card-title" id="chain-title">Pipeline</h2>
      <ol class="chain" id="chain"></ol>
      <p class="note" id="deploy-note" hidden></p>
    </section>
    <div class="stage">
      <section class="card player-card" aria-label="Player">
        <iframe id="player-frame" title="Player" hidden></iframe>
        <p class="placeholder" id="player-empty">The player appears here once the stack is deployed.</p>
      </section>
      <section class="card" id="detail" aria-live="polite"></section>
    </div>
    <section class="card" aria-labelledby="controls-title">
      <h2 class="card-title" id="controls-title">Controls</h2>
      <div id="controls"></div>
      <div class="confirm" id="confirm" hidden>
        <label for="confirm-input">Type <b>destroy</b> to tear down the whole stack</label>
        <input id="confirm-input" autocomplete="off" spellcheck="false">
        <button class="act danger" id="confirm-go" type="button">Tear down</button>
        <button class="act" id="confirm-cancel" type="button">Cancel</button>
      </div>
    </section>
    <section class="card" aria-labelledby="endpoints-title">
      <h2 class="card-title" id="endpoints-title">Endpoints</h2>
      <dl class="endpoints" id="endpoints"></dl>
    </section>
  </main>
  <aside class="card panel" id="panel" aria-label="Logs">
    <div class="tabs" role="tablist" id="tabs"></div>
    <pre class="log" id="log" tabindex="0"></pre>
  </aside>
</div>
<script type="module" src="app.js"></script>
</body>
</html>
```

- [ ] **Step 2: `app.css`**

Move the `@font-face`, `:root` tokens, `body`, `.card`, `.card-title`, `.chip`, `.dot`, `button.act`, `.confirm`, `pre.log`, `.note` and `.banner` rules from the old `index.html` unchanged, then add:
```css
.top { display: flex; align-items: center; gap: 12px; padding: 18px 20px; flex-wrap: wrap; }
.top h1 { font-size: 18px; font-weight: 600; margin: 0; }
.spacer { flex: 1; }
.chip.muted { color: var(--muted); }
.chip.button { cursor: pointer; font: inherit; font-size: 12px; }
.verdict { display: inline-flex; align-items: center; gap: 7px; font-weight: 600; font-size: 14px;
  padding: 5px 12px; border-radius: 999px; border: 1px solid var(--border); background: var(--surface); }
[data-health="ok"] > .dot, .dot[data-health="ok"] { background: var(--ok); }
[data-health="warn"] > .dot, .dot[data-health="warn"] { background: var(--warn); }
[data-health="bad"] > .dot, .dot[data-health="bad"] { background: var(--danger); }
[data-health="busy"] > .dot { background: var(--accent); }
.verdict[data-health="bad"] { border-color: #fbd3ce; background: #fef4f2; color: #912018; }

.layout { display: grid; grid-template-columns: minmax(0, 1fr) 360px; gap: 16px; padding: 0 20px 32px; }
.layout.panel-hidden { grid-template-columns: minmax(0, 1fr); }
.layout.panel-hidden .panel { display: none; }
.main { display: grid; gap: 16px; align-content: start; }
.stage { display: grid; grid-template-columns: minmax(0, 1.3fr) minmax(0, 1fr); gap: 16px; }

.chain { list-style: none; margin: 0; padding: 0; display: flex; gap: 6px; overflow-x: auto; }
.node-wrap { display: flex; align-items: center; gap: 6px; flex: 1 1 0; min-width: 120px; }
.node { flex: 1; text-align: left; font: inherit; cursor: pointer; background: var(--surface);
  border: 1px solid var(--border); border-radius: 10px; padding: 9px 10px; display: grid; gap: 3px; }
.node[aria-pressed="true"] { outline: 2px solid var(--accent); outline-offset: 1px; }
.node[data-health="bad"] { border-color: #fbd3ce; background: #fef4f2; }
.node[data-health="warn"] { border-color: #fbdca7; background: #fffcf5; }
.node-title { font-size: 12px; font-weight: 600; }
.node-state { font-size: 12px; font-weight: 600; color: var(--text-2); }
.node-summary { font-size: 11px; color: var(--muted); }
.arrow { color: var(--muted); }

.player-card { padding: 0; overflow: hidden; min-height: 240px; display: grid; }
.player-card iframe { width: 100%; height: 100%; min-height: 240px; border: 0; background: #0b0f17; }
.placeholder { color: var(--muted); margin: auto; padding: 24px; text-align: center; }
.facts { display: grid; grid-template-columns: max-content 1fr; gap: 6px 14px; font-size: 13px; margin: 12px 0; }
.facts dt { color: var(--muted); } .facts dd { margin: 0; white-space: pre-wrap; word-break: break-word; }
.error { color: #912018; font-size: 13px; }
.badge { margin-left: auto; font-size: 12px; font-weight: 600; }

.groups { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 16px; }
.group h3 { font-size: 12px; font-weight: 600; color: var(--muted); margin: 0 0 8px; text-transform: uppercase; letter-spacing: .04em; }
.group .actions { display: flex; flex-wrap: wrap; gap: 8px; }
.group .none { font-size: 12px; color: var(--muted); }

.endpoints { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 8px 14px; margin: 0; font-size: 13px; }
.endpoints dt { color: var(--muted); } .endpoints dd { margin: 0; display: flex; gap: 8px; align-items: center; min-width: 0; }
.endpoints .value { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.panel { position: sticky; top: 16px; align-self: start; display: grid; gap: 10px; max-height: calc(100vh - 32px); }
.tabs { display: flex; flex-wrap: wrap; gap: 4px; }
.tabs [role="tab"] { font: inherit; font-size: 12px; border: 1px solid var(--border); background: var(--surface);
  border-radius: 999px; padding: 4px 10px; cursor: pointer; }
.tabs [aria-selected="true"] { background: var(--accent-tint); border-color: #cfdcfa; color: var(--accent); }
.panel pre.log { max-height: none; overflow: auto; margin: 0; }

@media (max-width: 1000px) {
  .layout { grid-template-columns: minmax(0, 1fr); }
  .stage { grid-template-columns: minmax(0, 1fr); }
  .panel { position: static; max-height: 60vh; }
}
```

- [ ] **Step 3: `format.js` and `api.js`**

`format.js`:
```js
// DOM and number helpers. Data is always written as text, never as HTML.

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? '' : String(value));
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : String(child));
  }
  return node;
}

export function ago(seconds) {
  if (seconds === null || seconds === undefined) return '';
  const s = Math.round(seconds);
  if (s < 2) return 'just now';
  if (s < 90) return s + ' s ago';
  return Math.round(s / 60) + ' min ago';
}

const METRICS = {
  src_connected: ['Connected', (v) => (v >= 1 ? 'yes' : 'no')],
  src_bitrate: ['Bitrate', (v) => (v / 1e6).toFixed(1) + ' Mbps'],
  src_rtt: ['Round trip', (v) => v.toFixed(0) + ' ms'],
  src_not_recovered: ['Unrecovered packets / min', (v) => v.toFixed(0)],
  src_cc_errors: ['Continuity errors / min', (v) => v.toFixed(0)],
  ml_alerts: ['Active alerts', (v) => v.toFixed(0)],
  ml_fps: ['Input frame rate', (v) => v.toFixed(1) + ' fps'],
  ml_input_loss: ['Input loss', (v) => v.toFixed(0) + ' s / min'],
  mp_ingress_bytes: ['Ingest', (v) => ((v * 8) / 60 / 1e6).toFixed(1) + ' Mbps'],
  mp_egress_5xx: ['Egress 5xx / min', (v) => v.toFixed(0)],
  cf_requests: ['Requests / min', (v) => v.toFixed(0)],
  cf_5xx_rate: ['5xx rate', (v) => v.toFixed(1) + ' %'],
};

export function metricRow(key, value) {
  const [label, show] = METRICS[key] || [key, String];
  return [label, show(value)];
}
```
`api.js`:
```js
// The only module that talks to the livectl server.

const PATHS = { 'source-start': 'source/start', 'source-stop': 'source/stop' };

export async function getPipeline(offset) {
  const response = await fetch('api/pipeline?offset=' + offset, { cache: 'no-store' });
  if (!response.ok) throw new Error('the console answered ' + response.status);
  return response.json();
}

export async function post(action, body) {
  const response = await fetch('api/' + (PATHS[action] || action), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
  const data = await response.json().catch(() => ({}));
  return { ok: response.ok, status: response.status, data };
}
```

- [ ] **Step 4: `chain.js`, `node-detail.js`, `endpoints.js`**

`chain.js`:
```js
// The seven resources, left to right, each a button that selects it.
import { el } from './format.js';

export function renderChain(list, nodes, selected, onSelect) {
  list.replaceChildren(...nodes.map((node, index) => el('li', { class: 'node-wrap' },
    el('button', {
      class: 'node', type: 'button', 'data-node': node.id, 'data-health': node.health,
      'aria-pressed': String(node.id === selected), onclick: () => onSelect(node.id),
    },
      el('span', { class: 'node-title' }, node.title),
      el('span', { class: 'node-state', 'data-health': node.health }, el('i', { class: 'dot' }), node.state || '—'),
      el('span', { class: 'node-summary' }, node.summary)),
    index < nodes.length - 1 ? el('span', { class: 'arrow', 'aria-hidden': 'true' }, '→') : null)));
}
```
`node-detail.js`:
```js
// Everything the console knows about the selected resource.
import { ago, el, metricRow } from './format.js';

export function renderDetail(section, node, metricsAge) {
  if (!node) {
    section.replaceChildren(el('p', { class: 'placeholder' }, 'Nothing is deployed, so there are no resources to show.'));
    return;
  }
  const rows = [];
  for (const [key, value] of Object.entries(node.details)) {
    if (value === null || value === undefined || (Array.isArray(value) && value.length === 0)) continue;
    rows.push([key, Array.isArray(value) ? value.join('\n') : String(value)]);
  }
  for (const [key, value] of Object.entries(node.metrics)) {
    if (value !== null && value !== undefined) rows.push(metricRow(key, value));
  }
  const hasMetrics = Object.values(node.metrics).some((v) => v !== null && v !== undefined);
  section.replaceChildren(
    el('h2', { class: 'card-title' }, node.title,
      el('span', { class: 'badge', 'data-health': node.health }, node.state || node.health)),
    el('p', { class: 'summary' }, node.summary),
    node.error ? el('p', { class: 'error' }, node.error) : null,
    rows.length ? el('dl', { class: 'facts' }, rows.flatMap(([k, v]) => [el('dt', {}, k), el('dd', {}, v)])) : null,
    hasMetrics ? el('p', { class: 'note' }, 'Metrics from CloudWatch, ' + ago(metricsAge)) : null,
    node.console_url ? el('a', { href: node.console_url, target: '_blank', rel: 'noopener' }, 'Open in the AWS console') : null,
  );
}
```
`endpoints.js`:
```js
// Endpoints with copy buttons. Copy uses the value itself, never the text on screen (the old page copied a stray ↗).
import { el } from './format.js';

const ROWS = [
  ['ingest', 'SRT ingest'],
  ['player', 'Player'],
  ['manifest', 'HLS manifest'],
  ['passphrase_secret_arn', 'SRT passphrase (Secrets Manager)'],
];

async function copy(button, value) {
  try {
    await navigator.clipboard.writeText(value);
    button.textContent = 'Copied';
  } catch (error) {
    button.textContent = 'Copy blocked';
  }
  setTimeout(() => { button.textContent = 'Copy'; }, 1200);
}

export function renderEndpoints(list, endpoints) {
  list.replaceChildren(...ROWS.flatMap(([key, label]) => {
    const value = endpoints[key];
    return [
      el('dt', {}, label),
      el('dd', {},
        el('span', { class: 'value', title: value || '' }, value || '—'),
        value ? el('button', { class: 'copy', type: 'button', 'data-copy': key,
          onclick: (event) => copy(event.currentTarget, value) }, 'Copy') : null,
        value && key === 'player' ? el('a', { href: value, target: '_blank', rel: 'noopener' }, 'Open') : null),
    ];
  }));
}
```

- [ ] **Step 5: `controls.js` and `logs-panel.js`**

`controls.js`:
```js
// Controls grouped by what they act on. A button exists only when the server says the action is allowed now.
import { el } from './format.js';

export const GROUPS = [
  ['Infrastructure', [
    ['deploy', 'Deploy stack', 'primary', 'terraform apply: create or update the whole stack (a few minutes).'],
    ['teardown', 'Tear down stack', 'danger', 'terraform destroy: remove the whole stack.'],
    ['scan', 'Scan for leftovers', '', 'List billable resources still carrying the project name.'],
  ]],
  ['Broadcast', [
    ['go-live', 'Go live', 'primary', 'Start the flow, then the channel. Hourly billing starts (about 2 min).'],
    ['go-off-air', 'Go off air', '', 'Stop the channel, then the flow (about 2 min).'],
    ['source-start', 'Send test source', '', 'Push the FFmpeg test pattern with its UTC clock to the ingest.'],
    ['source-stop', 'Stop test source', '', 'Stop the FFmpeg test source.'],
  ]],
];

export function renderControls(container, actions, sourceRunning, onAction) {
  const visible = (name) => actions[name] === null && (name !== 'source-stop' || sourceRunning);
  container.replaceChildren(el('div', { class: 'groups' }, GROUPS.map(([title, items]) => {
    const shown = items.filter(([name]) => visible(name));
    return el('div', { class: 'group' },
      el('h3', {}, title),
      shown.length
        ? el('div', { class: 'actions' }, shown.map(([name, label, kind, help]) => el('button', {
          class: 'act ' + kind, type: 'button', 'data-action': name, title: help, onclick: () => onAction(name),
        }, label)))
        : el('p', { class: 'none' }, 'Nothing to do here right now.'));
  })));
}
```
`logs-panel.js`:
```js
// The right-hand panel. Plan 6 has the Jobs tab; Plan 7 adds one tab per resource.
import { el } from './format.js';

export class LogsPanel {
  constructor(tabs, pre) {
    this.tabs = tabs;
    this.pre = pre;
    this.active = 'jobs';
    this.jobKey = null;
    this.offset = 0;
    this.renderTabs([['jobs', 'Jobs']]);
  }

  renderTabs(list) {
    this.tabs.replaceChildren(...list.map(([id, label]) => el('button', {
      type: 'button', role: 'tab', 'data-tab': id, 'aria-selected': String(id === this.active),
      onclick: () => this.select(id),
    }, label)));
  }

  select(id) {
    this.active = id;
    for (const tab of this.tabs.querySelectorAll('[role=tab]')) tab.setAttribute('aria-selected', String(tab.dataset.tab === id));
  }

  showJob(job) {
    if (!job) {
      if (!this.pre.textContent) this.pre.textContent = 'No job has run since the console started.';
      return;
    }
    const key = job.name + '@' + job.started_at;
    if (key !== this.jobKey) {
      this.jobKey = key;
      this.pre.textContent = '';
      this.offset = 0;
    }
    if (job.lines.length) {
      this.pre.textContent += job.lines.join('\n') + '\n';
      this.pre.scrollTop = this.pre.scrollHeight;
    }
    this.offset = job.offset;
  }
}
```

- [ ] **Step 6: `app.js`**

```js
// Startup, polling and wiring. Each region re-renders only when its data changed, so a poll never steals focus.
import { getPipeline, post } from './api.js';
import { renderChain } from './chain.js';
import { renderControls } from './controls.js';
import { renderEndpoints } from './endpoints.js';
import { ago } from './format.js';
import { LogsPanel } from './logs-panel.js';
import { renderDetail } from './node-detail.js';

const POLL_MS = 2000;
const $ = (id) => document.getElementById(id);
const logs = new LogsPanel($('tabs'), $('log'));
const seen = {};
let data = null;
let selected = 'medialive_channel';
let lastUpdate = null;
let billingSince = null;
let lastFailedJob = null;

function changed(key, value) {
  const json = JSON.stringify(value);
  if (seen[key] === json) return false;
  seen[key] = json;
  return true;
}

function banner(text) {
  $('banner').textContent = text || '';
  $('banner').hidden = !text;
}

function renderHeader() {
  $('verdict').dataset.health = data.verdict.health;
  $('verdict-text').textContent = data.verdict.text;
  if (data.rate > 0) {
    if (billingSince === null) billingSince = Date.now();
    const minutes = Math.round((Date.now() - billingSince) / 60000);
    $('cost').textContent = '$' + data.rate.toFixed(2) + ' / h · ' + minutes + ' min';
  } else {
    billingSince = null;
    $('cost').textContent = '$0.00 / h';
  }
}

function renderPlayer() {
  const frame = $('player-frame');
  const url = data.endpoints.player;
  if (url && frame.getAttribute('src') !== url) frame.setAttribute('src', url);
  frame.hidden = !url;
  $('player-empty').hidden = Boolean(url);
}

function render() {
  renderHeader();
  const note = $('deploy-note');
  note.hidden = data.deployed && !data.note;
  note.textContent = data.note || (data.deployed ? '' : 'Nothing is deployed yet. Deploy stack creates it (a few minutes).');

  if (!data.nodes.some((n) => n.id === selected) && data.nodes.length) selected = data.nodes[0].id;
  // checked_at moves every second; leaving it out keeps the chain from re-rendering (and losing focus) on each poll.
  const stable = data.nodes.map(({ checked_at, ...rest }) => rest);
  if (changed('nodes', [stable, selected])) {
    renderChain($('chain'), data.nodes, selected, (id) => { selected = id; render(); });
    renderDetail($('detail'), data.nodes.find((n) => n.id === selected), data.metrics_age);
  }
  renderPlayer();
  const sourceRunning = Boolean(data.source && data.source.state === 'running');
  if (changed('actions', [data.actions, sourceRunning])) renderControls($('controls'), data.actions, sourceRunning, act);
  if (changed('endpoints', data.endpoints)) renderEndpoints($('endpoints'), data.endpoints);

  logs.showJob(data.job);
  const job = data.job;
  if (job && job.state === 'failed' && lastFailedJob !== job.started_at) {
    lastFailedJob = job.started_at;
    banner(job.name + ' failed: ' + (job.error || 'see the Jobs tab'));
  }
}

async function poll() {
  try {
    data = await getPipeline(logs.offset);
    lastUpdate = Date.now();
    render();
  } catch (error) {
    banner('The console is not answering (' + error.message + '). Is livectl ui still running?');
  }
}

async function act(name, body) {
  if (name === 'teardown' && !body) {
    $('confirm').hidden = false;
    $('confirm-input').focus();
    return;
  }
  const result = await post(name, body);
  if (!result.ok) {
    banner(result.data.error || name + ' was refused (' + result.status + ')');
    return;
  }
  banner('');
  delete seen.actions;
  poll();
}

$('confirm-go').addEventListener('click', async () => {
  await act('teardown', { confirm: $('confirm-input').value.trim() });
  $('confirm-input').value = '';
  $('confirm').hidden = true;
});
$('confirm-cancel').addEventListener('click', () => { $('confirm').hidden = true; });
$('panel-toggle').addEventListener('click', () => {
  const hidden = $('layout').classList.toggle('panel-hidden');
  $('panel-toggle').setAttribute('aria-expanded', String(!hidden));
});
setInterval(() => {
  if (lastUpdate !== null) $('freshness').textContent = 'Updated ' + ago((Date.now() - lastUpdate) / 1000);
}, 1000);

poll();
setInterval(poll, POLL_MS);
```

- [ ] **Step 7: Look at it**

Run `just ui-scenario off-air` and open `http://127.0.0.1:8766/`. Check by eye: the chain has seven nodes; the Channel is selected; four controls under two headings; no console errors in DevTools. Stop with Ctrl-C.

- [ ] **Step 8: Commit**

```bash
git add tools/livectl/site
git commit -m "feat: rebuild the console page as a control center"
```

---

### Task 4: Install Playwright for the UI tests

**Files:** none committed.

- [ ] **Step 1: Install the dev extra**

Run: `.venv/bin/pip install -q -e "tools[dev,ui]"`
Expected: installs `playwright`. Do **not** run `playwright install`: the tests use the Chrome already on this machine (`/usr/bin/google-chrome`) through `channel="chrome"`.

- [ ] **Step 2: Check the browser starts**

Run: `.venv/bin/python -c "from playwright.sync_api import sync_playwright as s; b = s().start().chromium.launch(channel='chrome'); print(b.version); b.close()"`
Expected: a Chrome version number.

---

### Task 5: Drive every scenario in the browser

**Files:**
- Create: `tools/tests/test_ui.py`

- [ ] **Step 1: Write the tests**

`tools/tests/test_ui.py`:
```python
"""Drive the console in Chrome through every scenario. Skipped when Playwright is not installed.

These tests exist because API tests once passed while the page offered a Start button next to NOT DEPLOYED.
They check what a person sees and can click, in every state.
"""

import threading

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")
expect = playwright_api.expect

from livectl.server import make_server  # noqa: E402
from scenarios import OUTPUTS, SCENARIOS, build  # noqa: E402

EXPECTED = {
    "not-deployed": ("Not deployed", ["deploy", "teardown", "scan"]),
    "deploying": ("Deploying…", []),
    "off-air": ("Off air", ["deploy", "teardown", "scan", "go-live"]),
    "going-live": ("Going live…", []),
    "on-air-no-source": ("On air · no source", ["scan", "go-off-air"]),
    "on-air-playing": ("On air · playing", ["scan", "go-off-air"]),
    "degraded": ("On air · source degraded", ["scan", "go-off-air"]),
    "partly-on": ("Partly on", ["scan", "go-live", "go-off-air"]),
    "probe-error": ("Unknown", ["deploy", "teardown", "scan", "go-live", "go-off-air"]),
}


@pytest.fixture(scope="module")
def browser():
    with playwright_api.sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        yield browser
        browser.close()


@pytest.fixture
def open_scenario(browser):
    opened = []

    def open_(name):
        scenario = build(name)
        server = make_server(scenario.console, port=0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        context = browser.new_context(permissions=["clipboard-read", "clipboard-write"])
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(f"http://127.0.0.1:{server.server_address[1]}/")
        opened.append((scenario, server, context, errors))
        return page, scenario

    yield open_
    for scenario, server, context, errors in opened:
        context.close()
        server.shutdown()
        server.server_close()
        scenario.close()
        assert errors == [], f"JavaScript errors: {errors}"


def test_every_scenario_is_covered():
    assert set(EXPECTED) == set(SCENARIOS)


@pytest.mark.parametrize("name", list(EXPECTED))
def test_each_state_shows_its_verdict_and_only_the_actions_that_can_work(open_scenario, name):
    page, _ = open_scenario(name)
    verdict, actions = EXPECTED[name]

    expect(page.locator("#verdict-text")).to_have_text(verdict, timeout=15000)
    shown = page.locator("#controls button[data-action]")
    expect(shown).to_have_count(len(actions))
    assert sorted(shown.evaluate_all("els => els.map(e => e.dataset.action)")) == sorted(actions)


def test_nothing_on_any_page_mentions_a_cli_flag(open_scenario):
    for name in ("not-deployed", "probe-error"):
        page, _ = open_scenario(name)
        expect(page.locator("#verdict-text")).not_to_have_text("connecting…")
        assert "--flow-arn" not in page.locator("body").inner_text()


def test_not_deployed_explains_what_to_do(open_scenario):
    page, _ = open_scenario("not-deployed")

    expect(page.locator("#deploy-note")).to_contain_text("Deploy stack")
    expect(page.locator("#player-empty")).to_be_visible()


def test_copy_puts_exactly_the_url_on_the_clipboard(open_scenario):
    page, _ = open_scenario("off-air")

    page.locator('#endpoints button[data-copy="player"]').click()

    assert page.evaluate("navigator.clipboard.readText()") == OUTPUTS["player_url"]


def test_the_ingest_copy_is_the_srt_url(open_scenario):
    page, _ = open_scenario("off-air")

    page.locator('#endpoints button[data-copy="ingest"]').click()

    assert page.evaluate("navigator.clipboard.readText()") == "srt://203.0.113.20:5000"


def test_clicking_a_node_shows_its_details(open_scenario):
    page, _ = open_scenario("on-air-no-source")

    page.locator('#chain button[data-node="srt_source"]').click()

    expect(page.locator("#detail h2")).to_contain_text("SRT Input Source")
    expect(page.locator('#chain button[data-node="srt_source"]')).to_have_attribute("aria-pressed", "true")
    expect(page.locator('#chain button[data-node="srt_source"]')).to_have_attribute("data-health", "bad")


def test_a_failed_probe_shows_its_error_on_the_node(open_scenario):
    page, _ = open_scenario("probe-error")

    page.locator('#chain button[data-node="mediaconnect_flow"]').click()

    expect(page.locator("#detail .error")).to_contain_text("AccessDenied")


def test_the_player_frame_loads_the_deployed_player(open_scenario):
    page, _ = open_scenario("off-air")

    expect(page.locator("#player-frame")).to_have_attribute("src", OUTPUTS["player_url"])


def test_teardown_needs_the_typed_word_then_runs_terraform(open_scenario):
    page, scenario = open_scenario("off-air")

    page.locator('#controls button[data-action="teardown"]').click()
    expect(page.locator("#confirm")).to_be_visible()
    page.locator("#confirm-input").fill("yes")
    page.locator("#confirm-go").click()
    expect(page.locator("#banner")).to_contain_text("destroy")
    assert scenario.commands == []

    page.locator('#controls button[data-action="teardown"]').click()
    page.locator("#confirm-input").fill("destroy")
    page.locator("#confirm-go").click()

    expect(page.locator("#log")).to_contain_text("terraform -chdir=envs/demo destroy")
    assert scenario.commands[0][2] == "destroy"


def test_a_poll_does_not_wipe_a_half_typed_confirmation(open_scenario):
    page, _ = open_scenario("off-air")
    page.locator('#controls button[data-action="teardown"]').click()
    page.locator("#confirm-input").fill("destr")

    page.wait_for_timeout(4500)  # two polls

    expect(page.locator("#confirm-input")).to_have_value("destr")


def test_the_log_panel_collapses(open_scenario):
    page, _ = open_scenario("off-air")

    page.locator("#panel-toggle").click()

    expect(page.locator("#panel")).to_be_hidden()
    expect(page.locator("#panel-toggle")).to_have_attribute("aria-expanded", "false")


def test_billing_states_show_a_rate(open_scenario):
    page, _ = open_scenario("partly-on")

    expect(page.locator("#cost")).to_contain_text("$0.29 / h")
```

- [ ] **Step 2: Run them**

Add one more test first, for the focus rule above:
```python
def test_a_poll_does_not_take_focus_off_the_selected_node(open_scenario):
    page, _ = open_scenario("off-air")
    page.locator('#chain button[data-node="cloudfront_cdn"]').focus()

    page.wait_for_timeout(4500)

    assert page.evaluate("document.activeElement.dataset.node") == "cloudfront_cdn"
```
Run: `just test-ui`
Expected: all PASS. Fix the page, not the tests, where they disagree. The fixture asserts that no JavaScript error happened in any test.

- [ ] **Step 3: Run the whole suite without Playwright's help**

Run: `just test`
Expected: PASS; `test_ui.py` runs too if Playwright is installed, and is skipped if not.

- [ ] **Step 4: Commit**

```bash
git add tools/tests/test_ui.py
git commit -m "test: drive the console in chrome through every scenario"
```

---

## Done when

- `just test-ui` passes, covering all nine scenarios, copy, node selection, teardown confirmation, panel collapse and cost.
- `just test` passes.
- `just ui-scenario <name>` shows each state for a person to look at.
- Not verified here (live checklist, Plan 7): the iframe playing real video, real AWS answers.
