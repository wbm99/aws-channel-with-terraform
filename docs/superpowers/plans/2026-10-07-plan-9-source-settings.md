# Source Settings Implementation Plan (Plan 9)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the FFmpeg test source tunable (video, audio, MPEG-TS identity, SRT latency) from a new Source page and from `just send`, with one Apply that restarts FFmpeg once, and a panel comparing what was sent with what MediaConnect received.

**Architecture:** `SourceSettings` (a frozen dataclass) plus `CHOICES` in `livectl/source.py` are the only place the allowed values live. The server keeps the current settings in memory and serves them with their choices, so the page builds its form from the server. `source/send-srt.sh` turns environment variables into FFmpeg arguments and validates them for `just send`. A new `livectl/received.py` normalises MediaConnect's `DescribeFlowSourceMetadata`.

**Tech Stack:** Python >= 3.10 (stdlib + boto3), pytest, moto; bash and FFmpeg; native ES modules; Playwright for Python (Chrome).

**Spec:** `docs/superpowers/specs/2026-10-07-source-settings-design.md`
**Branch:** `feat/source-settings` (from `main`).

## Global Constraints

- boto3 stays the only runtime dependency; the console stays on `http.server`.
- AWS clients, popen, runners and clocks are injected; only `cli.py` builds a `boto3.Session`.
- The passphrase never reaches a file, a default, a command line we build, a log line or an API response.
- H.264 only. Every value the console offers stays inside the channel's `AVC` / `HD` / `MAX_10_MBPS` input class: video bitrate 500–10000 kbps, resolution 1280x720 or 1920x1080.
- Allowed values (verbatim from the spec): pattern testcard|smpte|pal|black|standby; size 1280x720|1920x1080; fps 25|30|50|60; video 500–10000 kbps; keyframe interval 1|2|4 s; audio codec aac|mp2|ac3; audio bitrate 64|96|128|192|256 kbps; tone 440|1000|silence; service name and provider 1–60 characters with no control characters; program number 1–65535; SRT latency 20–8000 ms.
- Defaults: testcard, 1920x1080, 30 fps, 6000 kbps, 2 s, aac, 128 kbps, 1000, `livectl test source`, `livectl`, 1, 120 ms.
- Verified 2026-10-07 in AWS docs: MediaLive's MediaConnect and SRT inputs accept AAC, Dolby Digital (AC-3) and MPEG Audio (MP2); MediaConnect's `ProgramName` is the SDT service name.
- Nothing in this plan touches a real AWS account. Every task ends with `just test` green (Playwright runs when installed) and a Conventional Commits commit ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. `BITRATE=6M just send`, the form documented today, keeps working (normalised to 6000k, buffer 12000k). (Task 3)
2. AC-3 and MP2 at the extremes of the audio bitrate list (64k and 256k stereo) really encode in FFmpeg, not just pass validation. (Task 3, real-FFmpeg test)
3. A non-ASCII service name (`Jogo ão vivo`) reaches FFmpeg as one argument and counts as 12 characters, not bytes, both in Python and in the script. (Tasks 1 and 3)
4. Apply while the source has *exited* (FFmpeg failed: state `exited`, not running) saves the settings without restarting, and the next Send uses them. (Task 5)
5. The bitrate field cleared or typed as `6,5` in the browser: the page sends nothing invalid silently. The server's 400 names `video_kbps` and the message appears under that field. (Task 6)

---

### Task 1: `SourceSettings` and `CHOICES`

**Files:**
- Modify: `tools/livectl/source.py`
- Test: `tools/tests/test_source.py`

**Interfaces:**
- Produces:
  - `SourceSettings`: a frozen dataclass with fields, in this order: `pattern: str`, `size: str`, `fps: int`, `video_kbps: int`, `gop_seconds: int`, `audio_codec: str`, `audio_kbps: int`, `tone: str`, `service_name: str`, `service_provider: str`, `program_number: int`, `latency_ms: int`. The defaults are those in Global Constraints.
  - `SourceSettings.merged(self, partial: Mapping[str, object]) -> SourceSettings`.
  - `SourceSettings.to_env(self) -> dict[str, str]`.
  - `SourceSettings.to_dict(self) -> dict`.
  - `SettingsError(ValueError)`, with attributes `.field: str` and `.message: str`; `str(error) == message`.
  - `CHOICES: list[dict]`, one dict per field in dataclass order, each with `key`, `group` (`"Video"`, `"Audio"`, `"MPEG-TS"` or `"SRT"`) and `label`, plus by kind:
    - `kind: "enum"` with `values: [{"id", "label"}]` (ids are typed: `int` for fps, gop_seconds and audio_kbps, `str` otherwise);
    - `kind: "range"` with `min`, `max`, `step`, `unit`;
    - `kind: "text"` with `max_length: 60`.

    It is JSON-serialisable.
  - `PATTERNS` stays (the existing list of `(id, label)`). `CHOICES["pattern"]` is built from it.

- [ ] **Step 1: Write the failing tests** in `test_source.py`, under a `# --- settings` header:
  - `test_defaults_match_the_spec`:
    `SourceSettings().to_dict() == {"pattern": "testcard", "size": "1920x1080", "fps": 30, "video_kbps": 6000,
    "gop_seconds": 2, "audio_codec": "aac", "audio_kbps": 128, "tone": "1000",
    "service_name": "livectl test source", "service_provider": "livectl", "program_number": 1, "latency_ms": 120}`.
  - `test_merged_changes_only_the_given_keys`:
    `SourceSettings().merged({"fps": 25}).fps == 25`, and every other field equals the default.
  - `test_every_enum_value_and_both_range_ends_are_accepted`: parametrised over `CHOICES`. For each enum id, and for
    `min`/`max` of each range, `merged({key: value})` does not raise.
  - `test_bad_values_are_refused_naming_the_field`, parametrised:
    `("video_kbps", 499)`, `("video_kbps", 10001)`, `("program_number", 0)`, `("program_number", 65536)`,
    `("latency_ms", 19)`, `("fps", 24)`, `("size", "3840x2160")`, `("audio_codec", "opus")`,
    `("service_name", "x" * 61)`, `("service_name", "")`, `("service_name", "a\nb")`, `("video_kbps", "6000")`
    (a string where an int is needed), `("video_kbps", True)` (bool is not int here), `("colour", "red")` (an unknown
    key). Each raises `SettingsError` with `.field == key`.
  - `test_a_non_ascii_name_counts_characters`: `merged({"service_name": "Jogo ão vivo"})` is accepted;
    `"é" * 60` is accepted; `"é" * 61` is refused.
  - `test_to_env_names_the_script_variables`:
    `SourceSettings().merged({"video_kbps": 500, "tone": "silence"}).to_env() == {"PATTERN": "testcard",
    "SIZE": "1920x1080", "FPS": "30", "BITRATE": "500k", "GOP_SECONDS": "2", "AUDIO_CODEC": "aac",
    "AUDIO_BITRATE": "128k", "TONE": "silence", "SERVICE_NAME": "livectl test source",
    "SERVICE_PROVIDER": "livectl", "PROGRAM_NUMBER": "1", "SRT_LATENCY_MS": "120"}`.
  - `test_choices_follow_the_fields_and_serialise`:
    `[c["key"] for c in CHOICES] == [f.name for f in dataclasses.fields(SourceSettings)]`, and `json.dumps(CHOICES)`
    succeeds.

- [ ] **Step 2: Run** `just test-py -k "settings or choices or to_env or merged"`. Expected: they fail with
  `ImportError: cannot import name 'SourceSettings'`.

- [ ] **Step 3: Implement** in `source.py`. `merged()` checks keys against `CHOICES` and types with
  `type(value) is int` for int fields, then the enum membership, range or text rule. It raises on the first bad field
  with messages like `"Video bitrate must be between 500 and 10000 kbps."`, `"Frame rate must be one of 25, 30, 50,
  60."`, `"Service name must be 1 to 60 characters, without control characters."` and `"Unknown setting 'colour'."`.
  "Control character" means `unicodedata.category(ch) == "Cc"`. Ranges: video_kbps step 100 (the step is a UI hint
  only, not validated); program_number step 1; latency_ms step 10, unit `"ms"`. Keep `DEFAULT_PATTERN` as an alias
  of the default pattern until Task 2 removes its last user.

- [ ] **Step 4: Run** `just test-py`. Expected: all pass (existing tests untouched).

- [ ] **Step 5: Commit** `feat: describe the test source settings and their allowed values in one place`.

---

### Task 2: `SourceProcess` runs settings and restarts safely

**Files:**
- Modify: `tools/livectl/source.py`
- Test: `tools/tests/test_source.py`

**Interfaces:**
- Consumes: `SourceSettings`, `SourceSettings.to_env()` from Task 1.
- Produces:
  - `SourceProcess.start(*, host: str, port: int, passphrase: str, settings: SourceSettings = SourceSettings()) -> None`
    (the `pattern=` parameter is removed).
  - `SourceProcess.restart(*, host, port, passphrase, settings) -> None`: stop, then start, under a restart lock.
  - `SourceProcess.status() -> dict` with keys `state`, `exit_code`, `last_error`, `progress`, `settings`
    (`settings` is a `to_dict()`; `pattern` is removed).
  - `DEFAULT_PATTERN` removed.

- [ ] **Step 1: Write the failing tests:**
  - `test_the_settings_reach_the_script_environment`: start with `SourceSettings().merged({"fps": 50})`, then assert
    `popen.kwargs["env"]["FPS"] == "50"` and `status()["settings"]["fps"] == 50`.
  - `test_a_late_reader_from_the_old_process_changes_nothing`:
    - start with process A, whose `ignores_term=True`, and a `SourceProcess(grace=0.05)`;
    - `stop()`: A gets TERM, then KILL. Make A's `kill()` not set `_done` for this test (a subclass of `FakeProcess`),
      so its reader is still blocked;
    - start process B with a different secret, `NEWSECRET`, and settle `running` from a `frame=` line;
    - now release A (`A.finish()`, then wait briefly);
    - expect: the state is still `running`, `exit_code` is `None`, and a later B line containing `NEWSECRET` is stored
      as `***`.
  - `test_two_restarts_at_once_run_one_after_the_other`: a popen that records each launch and returns fresh
    `FakeProcess`es. Two threads call `restart()` at the same time. Expect exactly 3 launches in total (the initial
    start plus two), and at no moment two live processes. Check that with a counter the fake increments on launch
    and decrements on `terminate`, never exceeding 1.
  - Update the existing tests that pass `pattern=` or read `status()["pattern"]` to use `settings=` and
    `status()["settings"]["pattern"]`.

- [ ] **Step 2: Run** `just test-py -k source`. Expected: the new tests fail; the late-reader test fails on `state` or
  on redaction.

- [ ] **Step 3: Implement.**
  - `_pump(process, generation, secret)` redacts with its own `secret`, and takes the lock before every state write.
    It writes only when `generation == self._generation`.
  - `start()` increments `_generation` under the lock.
  - `stop()` keeps today's terminate/kill/join behaviour.
  - `restart()` holds a separate `threading.Lock` (`_restart_lock`) around `stop()` + `start()`.
  - Keep the docstring's passphrase note.

- [ ] **Step 4: Run** `just test-py -k source`. Expected: pass. Server tests that still use `pattern=` are fixed in
  Task 5; in this task, change only the call sites needed to stay green:
  - `_start_source` passes `settings=SourceSettings().merged({"pattern": pattern})`;
  - `scenarios.FakeSource.start(*, settings, **_)` still records `("start", settings.pattern)`, and `status()`
    returns `settings`, so the existing pattern browser tests pass unchanged until Task 5 replaces them.

  Run `just test` (not only `-k source`) before committing.

- [ ] **Step 5: Commit** `fix: keep a late FFmpeg reader from overwriting the restarted source, and start from settings`.

---

### Task 3: `send-srt.sh` takes every setting

**Files:**
- Modify: `source/send-srt.sh`
- Test: `tools/tests/test_send_script.py`

**Interfaces:**
- Consumes: the variable names from `SourceSettings.to_env()` (Task 1).
- Produces: the script contract for `just send` and the console. All variables are optional except `SRT_HOST` and
  `SRT_PASSPHRASE` when sending.

- [ ] **Step 1: Write the failing tests.**
  - Add a fixture `fake_ffmpeg(tmp_path)` that writes `tmp_path/bin/ffmpeg`, a bash script doing
    `printf '%s\n' "$@"`, and returns an env with that directory first on `PATH`, plus `SRT_HOST=127.0.0.1`,
    `SRT_PASSPHRASE=x`.
  - Add a helper `args(env) -> list[str]` that runs the script and returns stdout lines. For refusals, return
    `(returncode, stderr)`.
  - `test_the_gop_follows_the_frame_rate`: `FPS=25 GOP_SECONDS=2` → the args contain `-g`, `50`, `-keyint_min`, `50`,
    and `rate=25` appears in the `-i` lavfi source.
  - `test_the_buffer_is_twice_the_bitrate` with `BITRATE=500k` → `-b:v 500k -maxrate 500k -bufsize 1000k`.
  - `test_the_documented_megabit_form_still_works` with `BITRATE=6M` → `-b:v 6000k -bufsize 12000k`.
  - `test_the_service_identity_reaches_the_muxer` with `SERVICE_NAME="Jogo ão vivo"`, `PROGRAM_NUMBER=7` → the args
    contain `service_name=Jogo ão vivo` as one line, plus `service_provider=livectl`, `-mpegts_service_id`, `7`.
  - `test_silence_uses_a_silent_stereo_source` with `TONE=silence` → `anullsrc=r=48000:cl=stereo` is present, and
    `sine=` is absent. For `TONE=440`: `sine=frequency=440:sample_rate=48000`. Both have `-ac 2 -ar 48000`.
  - `test_audio_codec_and_bitrate` with `AUDIO_CODEC=mp2 AUDIO_BITRATE=192k` → `-c:a mp2 -b:a 192k`.
  - `test_latency_is_sent_in_microseconds`: the default gives `latency=120000` in the URL.
  - `test_bad_values_are_refused`, parametrised over `FPS=24`, `SIZE=3840x2160`, `BITRATE=12M`, `BITRATE=6.5M`,
    `GOP_SECONDS=3`, `AUDIO_CODEC=opus`, `AUDIO_BITRATE=100k`, `TONE=2000`, `PROGRAM_NUMBER=0`,
    `SRT_LATENCY_MS=10`, and `SERVICE_NAME` of 61 characters. Each exits 1, with stderr starting `error:` and naming
    the variable.
  - `test_every_audio_codec_really_encodes` (real FFmpeg; marked skipped without it, like the existing tests): for
    each codec in `aac`, `mp2`, `ac3` × `64k`, `256k`, get the args from the fake, then run the real `ffmpeg` with
    them after dropping `-re`, inserting `-t 1` before the output, and replacing the final SRT URL with
    `tmp_path/out.ts`. Expect returncode 0. If a pair fails, remove it from `CHOICES` and from the script's list
    rather than skipping it, and record that in the spec.
  - Keep the existing `--frame` tests.

- [ ] **Step 2: Run** `just test-py -k send_script`. Expected: the new tests fail.

- [ ] **Step 3: Implement.**
  - Under the header comment's "Optional environment" heading, list every variable with its allowed values, and say
    the console's list is `CHOICES` in `tools/livectl/source.py`: keep the two in step.
  - Add one validation helper per kind: `one_of VAR value…`, `between VAR min max`, `text VAR`. `text` counts with
    `LC_ALL=C.UTF-8` and refuses `[[:cntrl:]]`.
  - Build the FFmpeg command as a bash array.
  - Map the variables as the spec's TRD section lists.
  - `-c:v libx264 -preset veryfast -tune zerolatency` stays.

- [ ] **Step 4: Run** `just test-py -k send_script` and `just frame`. Expected: pass, and the PNG is written.

- [ ] **Step 5: Commit** `feat: let send-srt.sh take frame rate, GOP, audio and MPEG-TS service settings`.

---

### Task 4: what MediaConnect received

**Files:**
- Create: `tools/livectl/received.py`
- Modify: `tools/tests/stubs.py`
- Test: `tools/tests/test_received.py`

**Interfaces:**
- Produces:
  - `received(mediaconnect, flow_arn: str, *, clock_ms: Callable[[], int] = now_ms) -> dict`. It returns
    `{"programs": [{"name": str|None, "number": int|None, "streams": [{"type", "codec", "width", "height", "fps",
    "channels", "sample_rate"}]}], "messages": [str], "at": int}`.
    - `type` is MediaConnect's `StreamType`, lower-cased (`video`, `audio`, `data`).
    - `fps` is the `FrameRate` string converted to a float rounded to 2 decimals (`"30000/1001"` → 29.97, `"30"` →
      30.0); otherwise `None`.
  - In `stubs.py`: `source_metadata(name="livectl test source", number=1, fps="30", width=1920, height=1080,
    audio="aac") -> dict` (a full `DescribeFlowSourceMetadata` response); and
    `stub_aws(..., metadata: Optional[dict] = None)`, which adds `describe_flow_source_metadata` to the MediaConnect
    stub. The default is `{"FlowArn": FLOW_ARN, "Messages": [], "TransportMediaInfo": {"Programs": []}}`.
    `fail=("describe_flow_source_metadata",)` works like the other operations.

- [ ] **Step 1: Write the failing tests:**
  - `test_a_full_answer_is_normalised`: from `source_metadata(name="Match 1", fps="25")`, the result has
    `programs[0]["name"] == "Match 1"`, number 1, one video stream (`codec`, `width` 1920, `height` 1080, `fps` 25.0)
    and one audio stream (`channels` 2, `sample_rate` 48000).
  - `test_nothing_parsed_yet`: the default stub answer gives `{"programs": [], "messages": [], "at": <clock>}`.
  - `test_messages_only`: `Messages=[{"Code": "...", "Message": "No source"}]` → `messages == ["No source"]`.
  - `test_missing_fields_become_none`: a stream without `FrameResolution` or `FrameRate` gives `width`, `height` and
    `fps` all `None`.
  - `test_fractional_frame_rate`: `"30000/1001"` → 29.97.

- [ ] **Step 2: Run** `just test-py -k received`. Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement** `received.py`: a module docstring, `from __future__ import annotations`, and no boto3
  import.

- [ ] **Step 4: Run** `just test-py -k received`. Expected: pass.

- [ ] **Step 5: Commit** `feat: read what MediaConnect parsed from the incoming transport stream`.

---

### Task 5: server: settings, start, received; Control page follows

**Files:**
- Modify: `tools/livectl/server.py`, `tools/livectl/site/api.js`, `tools/livectl/site/app.js`,
  `tools/livectl/site/steps.js`, `tools/livectl/site/app.css`, `tools/tests/scenarios.py`
- Test: `tools/tests/test_server.py`, `tools/tests/test_ui.py`

**Interfaces:**
- Consumes:
  - `SourceSettings`, `SettingsError`, `CHOICES` (Task 1);
  - `SourceProcess.start(settings=…)`, `restart()`, `status()["settings"]` (Task 2);
  - `received()` (Task 4).
- Produces:
  - `Console.settings: SourceSettings` (default `SourceSettings()`) and `Console.settings_lock: threading.Lock`.
  - `GET /api/pipeline` has `source_settings: {"current": dict, "choices": CHOICES}`. `patterns` is removed.
  - `POST /api/source/settings`:
    - an invalid body: `400 {"error", "field"}`;
    - valid, with the source not `running`/`starting`: `200 {"saved": true, "current"}`;
    - valid, with it running: `202 {"saved": true, "restarted": true, "current"}`. The passphrase and target errors
      are those of today's `_start_source`.
  - `POST /api/source/start` ignores its body and uses `console.settings`.
  - `POST /api/source/pattern` is removed.
  - `GET /api/source/received`:
    - `{"state": "idle", "note": "The flow is not active."}` unless the flow is `ACTIVE`;
    - `{"state": "ok", **received()}`;
    - `{"state": "error", "note": "Could not read the source metadata (<ErrorType>)."}`.
  - `api.js`: `PATHS['source-settings'] = 'source/settings'`, `source-pattern` removed, and
    `getReceived() -> Promise<object>`.
  - `steps.js`: `summarise(settings, choices) -> string` (exported) builds
    `Test card · 1080p30 · 6 Mbps · AAC 128k · "livectl test source"`:
    - Mbps with at most one decimal (`0.5 Mbps`, `6 Mbps`);
    - `720p25` style;
    - the audio codec label upper-cased from the choice label;
    - the tone is not shown.
  - `scenarios.FakeSource`: `start(*, settings, **_)` records `("start", settings)`; `restart(*, settings, **_)`
    records `("restart", settings)`; `status()` returns `settings`.
  - `stub_aws(metadata=…)` is usable from `SCENARIOS` through a `metadata` key.

- [ ] **Step 1: Write the failing server tests** (in `test_server.py`, replacing the three pattern tests at lines
  512–545):
  - `test_the_payload_carries_the_settings_and_their_choices`: `current` equals `SourceSettings().to_dict()`;
    `choices[0]["key"] == "pattern"`.
  - `test_settings_saved_while_stopped_start_nothing`: POST `{"fps": 25}` → 200; `console.settings.fps == 25`; popen
    was never called.
  - `test_settings_applied_while_running_restart_once_with_the_merged_settings`: start, then POST
    `{"fps": 50, "service_name": "Match 1"}` → 202. Two launches in total, and the second launch's env has `FPS=50`
    and `SERVICE_NAME=Match 1`.
  - `test_settings_applied_after_the_source_exited_only_save_them`: a process that exits with code 1 and settles to
    `exited`; POST → 200, with no new launch; then `/api/source/start` launches with the saved settings.
  - `test_a_bad_setting_names_its_field`: POST `{"video_kbps": 20000}` → 400, `field == "video_kbps"`, and the
    settings are unchanged.
  - `test_start_uses_the_saved_settings`: POST settings `{"pattern": "pal"}` while stopped, then start → the env has
    `PATTERN=pal`.
  - `test_the_old_pattern_route_is_gone`: `/api/source/pattern` → 404.
  - `test_received_is_idle_while_the_flow_is_off`, `test_received_reports_the_programs`,
    `test_received_reports_an_error_without_its_body`: using `stub_console`, with `stub_aws(metadata=…)` and
    `fail=("describe_flow_source_metadata",)`. The error note contains the error type and not `stubbed failure`.

- [ ] **Step 2: Write the failing UI tests** (replace the three tests under `# --- test pattern` in `test_ui.py`):
  - `test_the_source_step_summarises_the_settings_and_links_to_the_source_page`: in `on-air-no-source`,
    `#source-summary` has the text `Test card · 1080p30 · 6 Mbps · AAC 128k · "livectl test source"`, there is no
    `#pattern`, and clicking `#source-summary a` makes the URL hash `#source`.
  - `test_send_test_source_on_control_starts_the_saved_settings`: click `#steps [data-action="source-start"]` →
    `scenario.console.source.calls[0][0] == "start"`, with the settings `== SourceSettings()`.

- [ ] **Step 3: Run** `just test-py -k "server or ui"`. Expected: the new tests fail.

- [ ] **Step 4: Implement.**
  - **`server.py`:**
    - `_start_source(console, *, restart: bool)` reads `console.settings` under the lock.
    - The `received` route uses `_snapshot(console)[1].flow_state`.
    - Remove `PATTERN_IDS` and the `DEFAULT_PATTERN` import.
  - **`app.js`:**
    - Remove `chosenPattern` and the `source-pattern` branch.
    - Pass `settings: data.source_settings` into the steps state.
  - **`steps.js`:**
    - Replace `patternPicker` with a `<p class="step-status" id="source-summary">`: the summary text, then
      ` — ` and `<a href="#source">Edit on Source</a>`.
    - The `#source` page itself comes in Task 6. Until then, the link only changes the hash.
  - **`scenarios.py`:** update `FakeSource`, and pass `metadata` through.

- [ ] **Step 5: Run** `just test`. Expected: all green.

- [ ] **Step 6: Commit** `feat: serve the source settings and apply them with one restart`.

---

### Task 6: the Source page

**Files:**
- Create: `tools/livectl/site/source-page.js`
- Modify: `tools/livectl/site/index.html`, `tools/livectl/site/app.js`, `tools/livectl/site/app.css`,
  `tools/tests/scenarios.py`
- Test: `tools/tests/test_ui.py`

**Interfaces:**
- Consumes:
  - the payload `source_settings` and `source.settings`, plus `/api/source/settings`, `/api/source/received` and
    `getReceived()` (Task 5);
  - `post()` (`api.js`) and `summarise()` (`steps.js`).
- Produces:
  - `class SourcePage { constructor(root: HTMLElement, { post, getReceived, banner, act }); render(data); visible(on: boolean) }`.
    `act` is app.js's existing action function, used for `source-start` and `source-stop`, so refusals, banners and
    re-renders behave exactly as on Control.
  - DOM contract for the tests:
    - nav link `.menu [data-page="source"]`, between Control and Live;
    - view `#view-source`;
    - `#source-form` with one control per `CHOICES` key, as `[name=<key>]` in four `<fieldset>`s, each with a
      `<legend>` naming its group;
    - a changed field has `data-changed` on its row;
    - `[data-error-for=<key>]` holds that field's message;
    - buttons `#source-apply`, `#source-discard`, `#source-stop`, `#source-reset`;
    - `#source-note` (after-Apply text, or the refusal);
    - `#source-received`, a table with rows `tr[data-row="video"|"audio"|"program"]`; a disagreeing row has
      `data-mismatch`;
    - `#source-received-note` for idle or error.
  - Scenarios:
    - `source-running` gains matching `metadata`;
    - new `source-received-mismatch`: running, with `source_metadata(name="Service01")`.
    - The spec's `source-stopped` and `source-refused` are the existing `on-air-no-source` and `off-air`;
      `source-dirty` is reached by editing in the test.
  - Add both new scenario rows to `EXPECTED`: `source-received-mismatch` →
    `("On air · playing", ["scan", "go-off-air", "source-stop"])`.

- [ ] **Step 1: Write the failing UI tests:**
  - `test_the_form_is_built_from_the_choices`: in `on-air-no-source`, at `#source`:
    - 12 controls;
    - the `fps` select offers `25, 30, 50, 60`;
    - `video_kbps` shows `6` with `min=0.5 max=10 step=0.1`;
    - `service_name` has `maxlength=60`.
  - `test_two_edits_make_one_apply_with_only_those_fields` (`source-running`):
    - set `fps` to 50, and `service_name` to `Match 1`;
    - `#source-apply` reads `Apply 2 changes`;
    - click it, and capture the request with `page.expect_request("**/api/source/settings")`: its JSON is
      `{"fps": 50, "service_name": "Match 1"}`;
    - the fake records one `("restart", …)` with those values;
    - `#source-note` contains `Restarting FFmpeg`;
    - then `#source-apply` is disabled and reads `Apply`.
  - `test_while_stopped_the_main_button_sends` (`on-air-no-source`): it reads `Send test source`. Change `pattern` to
    `smpte` and click: the calls are `[("start", settings)]`, with `settings.pattern == "smpte"`.
  - `test_an_invalid_bitrate_is_shown_under_its_field` (`source-running`): fill `video_kbps` with `20` → Apply →
    `[data-error-for="video_kbps"]` contains `between 500 and 10000`, and the field is focused. Then clear the field →
    Apply → the same element shows a message, and no `restart` was recorded.
  - `test_discard_and_reset`: edit `fps`; Discard → back to 30, and the button is disabled. Reset to defaults while
    the settings are non-default (POST `{"fps": 25}` through `scenario.console` before opening) → the form shows 30,
    the button reads `Send test source`, and nothing is sent until it is clicked.
  - `test_a_poll_never_overwrites_an_edited_field`: edit `service_name`, wait 2.5 s (more than one poll) → the value
    is still the edit.
  - `test_another_tabs_change_reaches_an_unedited_form`: open the page; then
    `scenario.console.settings = scenario.console.settings.merged({"fps": 60})` → within 5 s the `fps` field shows 60.
  - `test_sent_and_received_agree` (`source-running`): the three rows are present, and none has `data-mismatch`.
  - `test_a_renamed_service_is_highlighted` (`source-received-mismatch`): `tr[data-row="program"]` has
    `data-mismatch`, and the row shows both `livectl test source` and `Service01`.
  - `test_received_says_why_when_idle` (`off-air`): `#source-received-note` contains `not active`.
  - `test_send_is_refused_with_the_controls_message` (`off-air`): `#source-apply` is disabled, and `#source-note`
    shows the `source-start` refusal from the payload.
  - `test_the_source_page_stacks_on_a_narrow_window`: viewport 600×900; the received card's top is greater than the
    form's bottom.

- [ ] **Step 2: Run** `just test-ui -k source`. Expected: fail (no `#view-source`).

- [ ] **Step 3: Implement.**
  - **The form:**
    - `video_kbps` is shown in Mbps (`value / 1000`; sent as `Math.round(input * 1000)`).
    - An empty or NaN number field is sent as `null`, so the server's 400 names it.
    - Enum values are sent with their id type (`Number` for int ids).
  - **Unsaved changes** are a comparison of each field with the `current` the form was last synced from. A focused or
    changed field is never overwritten by `render()`.
  - **Received polling:**
    - `getReceived()` every 5 s, only while `visible(true)` and (the source state is `running` or the `srt_source`
      node is `CONNECTED`);
    - stopped when hidden;
    - in `app.js`, `showPage()` calls `sourcePage.visible(page() === 'source')`.
  - **Mismatch rules:**
    - codec: compare after aliasing `h264`/`avc` → `h264`, `aac` → `aac`, `mp2`/`mpeg audio`/`mp1` → `mp2`,
      `ac3`/`dolby digital` → `ac3`, case-insensitive; an unknown string is shown as-is and is not a mismatch;
    - resolution: compare width×height;
    - fps: compare within 0.01;
    - program: name and number must both match;
    - audio sample rate 48000 and 2 channels are expected.
  - **Copy:**
    - after Apply: `Restarting FFmpeg — SRT reconnects, expect a few seconds of slate.`;
    - idle and error: the server's `note`.
  - **CSS:** two columns above 900 px, stacked below. Reuse the existing `.card`, `.act` and `.primary` classes.

- [ ] **Step 4: Run** `just test-ui`. Expected: all pass, including the existing ones (`EXPECTED` covers every
  scenario).

- [ ] **Step 5: Click through by hand** (memory rule: verify a UI by driving it). For each of
  `source-running`, `source-received-mismatch`, `on-air-no-source` and `off-air`, run `just ui-scenario <name>` and
  open `#source`:
  - edit, Apply, Discard, Reset, Stop and Send;
  - a narrow window;
  - keyboard only: Tab reaches every field and button in order, and the change markers are announced.

  Fix what feels wrong before committing.

- [ ] **Step 6: Commit** `feat: add a Source page to tune the test source and compare it with what MediaConnect received`.

---

### Task 7: docs and the live checklist

**Files:**
- Modify: `README.md`, `AGENTS.md`, `docs/superpowers/specs/2026-10-07-source-settings-design.md`

- [ ] **Step 1: README.**
  - In the console section, replace the pattern-picker paragraph (line ~159) with the Source page: the groups,
    Apply, and sent vs received.
  - Under `just send`, add one example: `FPS=25 SERVICE_NAME="Match 1" just send`. Also add a line pointing at the
    script's header for the full list.
  - Under *Verify the console live*, add the four checks from the spec's Docs section. Number them after the
    existing ones.
- [ ] **Step 2: AGENTS.md.** In the Python conventions, add one line: "`SourceSettings` and `CHOICES` in
  `livectl/source.py` hold the allowed source values; `source/send-srt.sh` mirrors them (its header says so). Change
  both together."
- [ ] **Step 3: Spec.**
  - Set Status to `Approved; planned in docs/superpowers/plans/2026-10-07-plan-9-source-settings.md`.
  - Under *To verify*, record both answers with their doc links:
    - <https://docs.aws.amazon.com/medialive/latest/ug/inputs-supported-codecs-by-input-type.html>
    - <https://docs.aws.amazon.com/mediaconnect/latest/ug/monitor-with-source-stream-monitoring.html>
  - Note the scenario mapping from Task 6.
- [ ] **Step 4: Run** `just fmt`, `just validate` and `just test`. Expected: all green; no Terraform changes.
- [ ] **Step 5: Commit** `docs: the Source page, the new send variables and live checks for restarts`.
