# Ad Breaks with SCTE-35 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cue SCTE-35 ad breaks from the console's Live page, carry them through MediaLive and MediaPackage into the HLS manifest, and have the player overlay an operator-supplied clip for the break's duration; settings live in a new Configuration → Ads tab.

**Architecture:** The console writes `envs/demo/console.auto.tfvars.json` and `envs/demo/ads/clip.mp4`; Terraform reads both at deploy, uploads the clip with the player and exposes an `ads` output that livectl reads like its other targets. A cue is one immediate `BatchUpdateSchedule` splice insert; the console and the player both learn about it only from `EXT-X-DATERANGE` in the manifest.

**Tech Stack:** Terraform (`aws` 6.65.0, `awscc` 1.102.0), Python 3 stdlib + boto3, vanilla JS console, hls.js 1.5.15, pytest + moto, Playwright.

**Spec:** `docs/superpowers/specs/2026-10-11-ad-breaks-design.md`

## Global Constraints

- boto3 is livectl's only runtime dependency. No multipart library: the clip is a raw request body.
- AWS clients and clocks are passed in, never constructed in logic (`cue_ad_break(medialive, ...)`, `AdBreaks(clock, clock_ms)`).
- `description` on every new Terraform variable and output; `just fmt` before each Terraform commit.
- `from __future__ import annotations`, a module docstring on every new Python file, frozen dataclasses for values.
- Config file: `envs/demo/console.auto.tfvars.json`; clip: `envs/demo/ads/clip.mp4`, written to the file as `"ads/clip.mp4"`.
- Duration modes exactly `"clip"` and `"per_trigger"`; default `"per_trigger"`. Per-trigger range 5–180 s.
- Clip limit 50 MB (`50 * 1024 * 1024`), `Content-Type: video/mp4`, bytes 4–8 equal `b"ftyp"`.
- Splice `Duration = round(seconds * 90000)`; `SpliceEventId = (clock_ms() // 1000) % 2**32`.
- "Not seen" after 60 s. Copy: `not seen in manifest after 60 s — check SCTE settings in package/encode`.
- Every UI state gets a Playwright scenario and is clicked through (memory: verify a UI by driving it).
- Commits: Conventional Commits, imperative, lower case after the prefix, with the `Co-Authored-By` trailer.
- Nothing in this plan runs `apply`, `start` or any live AWS call. Live verification is a separate, user-requested session.

## Review Focus

1. **A stack deployed before this feature** (no `ads` output): the cue is refused with "Deploy once more to enable ad breaks", the Ads tab still loads. Test in Task 6.
2. **The clip deleted by hand after saving**: the Ads tab shows "no clip" rather than a stale preview, and `pending` stays computable. Test in Task 3.
3. **A second cue while a break runs** (double-click, two tabs): refused with the seconds left, MediaLive called once. Test in Task 6.
4. **An upload larger than 50 MB**: refused from `Content-Length` before the body is read, and the connection closed. Test in Task 6.
5. **A viewer who joins mid-break or reloads during one**: sees the remainder once, never a replay of a finished break. Test in Task 9.

---

### Task 1: Player module carries the ad settings and the clip

**Files:**
- Modify: `modules/player/variables.tf`, `modules/player/main.tf`, `modules/player/outputs.tf`
- Create: `modules/player/tests/fixtures/clip.mp4` (any bytes; `printf 'fixture' > ...`), `modules/player/site/ads.js` (placeholder `// Ad break scheduling; filled in by Task 9.`)
- Modify: `envs/demo/variables.tf`, `envs/demo/main.tf:45-50`, `envs/demo/outputs.tf`, `.gitignore`
- Test: `modules/player/tests/player.tftest.hcl`

**Interfaces:**
- Produces: player variable `ads` (object below); output `ads = { clip_md5 = string|null, clip_seconds = number|null, duration_mode = string }`; env output `ads` re-exporting it; `config.json` key `adClip` (`"/ads/clip.mp4"` or `null`); S3 object `ads.js`.

- [ ] **Step 1: Write the failing tests** in `player.tftest.hcl` (all `command = plan`):
  - `no_clip_by_default`: `length(aws_s3_object.ad_clip) == 0`; `jsondecode(aws_s3_object.config.content).adClip == null`; `output.ads.clip_md5 == null`; `output.ads.duration_mode == "per_trigger"`.
  - `clip_is_uploaded_and_announced` (`ads = { clip = "tests/fixtures/clip.mp4", clip_seconds = 24.5, duration_mode = "clip" }`): `length(aws_s3_object.ad_clip) == 1`; `aws_s3_object.ad_clip[0].key == "ads/clip.mp4"`; `content_type == "video/mp4"`; `adClip == "/ads/clip.mp4"`; `output.ads.clip_md5 == filemd5("tests/fixtures/clip.mp4")`; `output.ads.clip_seconds == 24.5`.
  - `ads_js_is_uploaded`: `aws_s3_object.ads_js.key == "ads.js"`, `content_type == "text/javascript"`.
  - Four `expect_failures = [var.ads]` runs: unknown mode `"sometimes"`; clip `"tests/fixtures/clip.mov"`; clip that does not exist `"tests/fixtures/missing.mp4"`; clip without `clip_seconds`; mode `"clip"` with no clip.

- [ ] **Step 2: Run** `terraform -chdir=modules/player test` — Expected: FAIL (`ad_clip`/`ads_js`/`var.ads` not declared).

- [ ] **Step 3: Implement.**
  - `variable "ads"`: type `object({ clip = optional(string), clip_seconds = optional(number), duration_mode = optional(string, "per_trigger") })`, `default = {}`, description from the spec. One `validation` block per rule, each with its own message. Terraform's `||` does not short-circuit, so guard file checks with a conditional: `var.ads.clip == null ? true : (endswith(var.ads.clip, ".mp4") && fileexists(var.ads.clip))`.
  - `aws_s3_object.ad_clip` (`count`), `aws_s3_object.ads_js` (same shape as `index`), `config` content `jsonencode({ manifestPath = var.manifest_path, adClip = var.ads.clip == null ? null : "/ads/clip.mp4" })`.
  - Output `ads` with `clip_md5 = var.ads.clip == null ? null : filemd5(var.ads.clip)`.
  - `envs/demo`: `variable "ads"` with the same type and default, no validation, description "Ad break settings written by the console (console.auto.tfvars.json); see modules/player."; pass `ads = merge(var.ads, { clip = var.ads.clip == null ? null : "${path.root}/${var.ads.clip}" })`; `output "ads"` = `module.player.ads`.
  - `.gitignore` under `# Terraform`: `envs/demo/*.auto.tfvars.json` and `envs/demo/ads/`.

- [ ] **Step 4: Run** `just fmt && terraform -chdir=modules/player test && just validate` — Expected: all pass.

- [ ] **Step 5: Commit** `feat: let the player module deploy an optional ad clip and report the ad settings`.

---

### Task 2: SCTE-35 through encode and package

**Files:**
- Modify: `modules/encode/main.tf:82-116`, `modules/package/main.tf:29-40`
- Test: `modules/encode/tests/encode.tftest.hcl`, `modules/package/tests/package.tftest.hcl`

**Interfaces:** none consumed or produced in code; the manifest gains `EXT-X-DATERANGE` and `EXT-X-PROGRAM-DATE-TIME` live.

- [ ] **Step 1: Write the failing tests.**
  - encode `passes_scte35_through_to_mediapackage`: every output's `...hls_settings[0].standard_hls_settings[0].m3u8_settings[0].scte35_behavior == "PASSTHROUGH"` (use `alltrue([for o in ...outputs : ...])`); `...hls_group_settings[0].ad_markers == ["ELEMENTAL_SCTE35"]`.
  - package `turns_splice_inserts_into_dateranges`: `awscc_mediapackagev2_origin_endpoint.hls.segment.scte.scte_filter == ["SPLICE_INSERT"]`; `hls_manifests[0].scte_hls.ad_marker_hls == "DATERANGE"`; `hls_manifests[0].program_date_time_interval_seconds == 6`.

- [ ] **Step 2: Run** `terraform -chdir=modules/encode test` and `terraform -chdir=modules/package test` — Expected: the two new runs FAIL.

- [ ] **Step 3: Implement** the attributes exactly as asserted. `m3u8_settings` is no longer empty, which also removes the empty-block risk noted in AGENTS.md.

- [ ] **Step 4: Run** the two test commands and `just validate` — Expected: PASS.

- [ ] **Step 5: Commit** `feat: carry scte-35 splice inserts from medialive into the hls manifest`.

---

### Task 3: `livectl/config.py` — the console's settings file

**Files:**
- Create: `tools/livectl/config.py`
- Test: `tools/tests/test_config.py`

**Interfaces:**
- Produces:
  - Constants `CONFIG_FILE = "console.auto.tfvars.json"`, `CLIP_PATH = "ads/clip.mp4"`, `MAX_CLIP_BYTES = 50 * 1024 * 1024`, `DURATION_MODES = ("clip", "per_trigger")`, `MIN_BREAK_S = 5`, `MAX_BREAK_S = 180`.
  - `class ConfigError(RuntimeError)` with `.message: str`, `.field: Optional[str]`.
  - `@dataclass(frozen=True) class AdsSettings: clip: Optional[str] = None; clip_seconds: Optional[float] = None; duration_mode: str = "per_trigger"` with `to_dict() -> dict` (keys exactly as Terraform's) and `classmethod from_dict(d: dict) -> AdsSettings` (validates like Terraform, except file existence; raises `ConfigError(message, field)`), and `with_mode(mode: str) -> AdsSettings`.
  - `class ConfigStore(tf_dir: str)`: `read() -> dict` (`{}` if absent; `ConfigError(field=None)` if not a JSON object); `ads() -> AdsSettings` (a `clip` whose file is gone reads as no clip); `clip_md5() -> Optional[str]`; `clip_size() -> Optional[int]`; `save_ads(ads: AdsSettings) -> None`; `save_clip(data: bytes, seconds: float) -> AdsSettings`; `remove_clip() -> AdsSettings`.
  - `pending(saved: AdsSettings, saved_md5: Optional[str], deployed: Optional[dict]) -> bool`.

- [ ] **Step 1: Write the failing tests** (`tmp_path` as `tf_dir`):
  - `test_no_file_means_default_settings`: `ConfigStore(tmp).ads() == AdsSettings()`.
  - `test_saving_keeps_keys_it_does_not_own`: file `{"other": 1}` → `save_ads(AdsSettings(duration_mode="per_trigger"))` → JSON has `other == 1` and `ads.duration_mode == "per_trigger"`.
  - `test_a_malformed_file_is_refused_not_overwritten`: file `"{"` → `read()` raises `ConfigError`; `save_ads(...)` raises and the file still reads `"{"`.
  - `test_saving_a_clip_writes_it_and_its_length`: `save_clip(b"\0\0\0\x18ftypisom", 24.5)` → `(tmp/"ads/clip.mp4").read_bytes()` matches; `ads().clip == "ads/clip.mp4"`, `clip_seconds == 24.5`, mode unchanged.
  - `test_writes_leave_no_temporary_files`: after `save_clip` and `save_ads`, the only entries are the config file and `ads/clip.mp4`.
  - `test_removing_the_clip_falls_back_to_per_trigger`: clip mode → `remove_clip()` → file gone, `ads() == AdsSettings()`.
  - `test_a_clip_deleted_by_hand_reads_as_no_clip` (Review Focus 2): save clip, delete the file → `ads().clip is None`, `clip_md5() is None`, `clip_size() is None`.
  - `test_validation_matches_terraform`: parametrized `from_dict` failures with `field`: mode `"sometimes"` → `duration_mode`; clip `"x.mov"` → `clip`; clip without seconds → `clip_seconds`; seconds `0` → `clip_seconds`; mode `"clip"` without clip → `duration_mode`.
  - `test_pending`: nothing deployed (`None`) → `False`; equal → `False`; mode differs → `True`; same length but different md5 → `True`.

- [ ] **Step 2: Run** `pytest -q tools/tests/test_config.py` — Expected: FAIL (`ModuleNotFoundError: livectl.config`).

- [ ] **Step 3: Implement.** Atomic writes: `tempfile.NamedTemporaryFile(dir=target.parent, delete=False)` then `os.replace`. The file is written with `indent=2` and a trailing newline, so a person can read it. `pending` compares `saved_md5`, `clip_seconds` and `duration_mode` with the deployed dict.

- [ ] **Step 4: Run** `pytest -q tools/tests/test_config.py` — Expected: PASS.

- [ ] **Step 5: Commit** `feat: store the console's ad settings where terraform reads them`.

---

### Task 4: Read ad markers from the manifest, and the `ads` output

**Files:**
- Modify: `tools/livectl/manifest.py`, `tools/livectl/pipeline.py:236-335`, `tools/livectl/targets.py`
- Test: `tools/tests/test_manifest.py`, `tools/tests/test_pipeline.py`, `tools/tests/test_targets.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class DateRange: id: str; start: str; seconds: Optional[float]` and `date_ranges(playlist: str) -> tuple[DateRange, ...]` (only tags carrying `SCTE35-OUT`; `seconds` from `DURATION`, else `PLANNED-DURATION`, else `None`).
  - `ManifestCheck` gains a last field `breaks: tuple[DateRange, ...] = ()`, filled from the variant playlist.
  - `Pipeline.last_check: Optional[ManifestCheck]`, the latest check `nodes()` made (`None` before any).
  - `Targets.ads: Optional[dict] = None`, from the `ads` output.

- [ ] **Step 1: Write the failing tests.**
  - `test_dateranges_with_scte35_out_are_breaks`: a playlist with one `#EXT-X-DATERANGE:ID="4",START-DATE="2026-10-11T12:03:10.000Z",DURATION=30.0,SCTE35-OUT=0xFC...` and one DATERANGE without `SCTE35-OUT` → `(DateRange("4", "2026-10-11T12:03:10.000Z", 30.0),)`.
  - `test_planned_duration_is_the_fallback` → `seconds == 24.0`; `test_no_duration_gives_none` → `seconds is None`.
  - `test_the_check_carries_the_variant_breaks`: `ManifestWatcher` with a fake fetch whose variant has one break → `check(url).breaks[0].id == "4"`.
  - `test_the_pipeline_keeps_its_last_manifest_check` (in `test_pipeline.py`, on a running-channel stub): `nodes(targets)` then `pipeline.last_check.sequence` is set.
  - `test_the_ads_output_is_read`: runner outputs include `ads` → `resolve_targets(...).ads == {...}`; absent → `None`.

- [ ] **Step 2: Run** `pytest -q tools/tests/test_manifest.py tools/tests/test_pipeline.py tools/tests/test_targets.py` — Expected: the new tests FAIL.

- [ ] **Step 3: Implement.** Parse attributes with a small regex for `KEY=value` / `KEY="value"` pairs. Quoted values may contain commas.

- [ ] **Step 4: Run** the same command — Expected: PASS.

- [ ] **Step 5: Commit** `feat: read scte-35 ad markers from the variant playlist`.

---

### Task 5: Cue a break, and track it until it shows up

**Files:**
- Modify: `tools/livectl/control.py`
- Create: `tools/livectl/ads.py`
- Test: `tools/tests/test_control.py`, `tools/tests/test_ads.py`

**Interfaces:**
- Consumes: `DateRange`, `ManifestCheck.breaks` (Task 4).
- Produces:
  - `control.NotRunning(RuntimeError)`; `control.cue_ad_break(medialive, channel_id: str, seconds: float, *, clock_ms: Callable[[], int]) -> int` (returns the event ID; raises `NotRunning` unless `describe_channel` says `RUNNING`).
  - `ads.NOT_SEEN_AFTER_S = 60`; `@dataclass(frozen=True) class Cue: event_id: int; seconds: float; cued_at_ms: int; cued_at: float; baseline: frozenset[str]`.
  - `class AdBreaks(clock: Callable[[], float], clock_ms: Callable[[], int])`: `seconds_left() -> Optional[float]` (`None` when no break runs); `cued(event_id: int, seconds: float, check: Optional[ManifestCheck]) -> None` (baseline = IDs in `check.breaks`); `observe(check: Optional[ManifestCheck]) -> None` (records `seen_at_ms` at the first ID not in the baseline); `status() -> Optional[dict]` with keys `event_id, seconds, cued_at_ms, seen_at_ms, state` (`"waiting" | "seen" | "not_seen"`), `seconds_left`.

- [ ] **Step 1: Write the failing tests.**
  - `test_a_cue_is_one_immediate_splice_insert` (StubClient medialive with `describe_channel` RUNNING and `batch_update_schedule={}`, `clock_ms=lambda: 1_760_184_190_000`): the `batch_update_schedule` call has `ChannelId`, one action named `ad-break-1760184190`, `ScheduleActionStartSettings == {"ImmediateModeScheduleActionStartSettings": {}}`, `Scte35SpliceInsertSettings == {"SpliceEventId": 1760184190, "Duration": 2_700_000}` for `seconds=30`.
  - `test_a_fractional_length_rounds_to_ticks`: `24.5` → `Duration == 2_205_000`.
  - `test_no_cue_unless_running`: channel `IDLE` → `NotRunning`, no `batch_update_schedule` call.
  - In `test_ads.py`, with a fake monotonic clock and a fake `clock_ms`:
    - `test_a_new_daterange_marks_the_cue_seen`: baseline `{"1"}`, observe a check with `{"1","2"}` → `state == "seen"`, `seen_at_ms` set.
    - `test_an_old_daterange_does_not_count`: observe `{"1"}` only → `"waiting"`.
    - `test_after_sixty_seconds_unseen_is_reported`: advance 61 s → `"not_seen"`.
    - `test_seconds_left_counts_down_then_clears`: cue 30 s, advance 10 → `20.0`; advance 25 → `None`.

- [ ] **Step 2: Run** `pytest -q tools/tests/test_control.py tools/tests/test_ads.py` — Expected: FAIL.

- [ ] **Step 3: Implement** as specified. `ads.py`'s docstring says why the baseline exists: no assumption about how MediaPackage forms DATERANGE IDs.

- [ ] **Step 4: Run** the same command — Expected: PASS.

- [ ] **Step 5: Commit** `feat: cue scte-35 ad breaks on the medialive channel and watch for their markers`.

---

### Task 6: Server routes and rules

**Files:**
- Modify: `tools/livectl/actions.py`, `tools/livectl/server.py`
- Test: `tools/tests/test_actions.py`, `tools/tests/test_server.py`

**Interfaces:**
- Consumes: `ConfigStore`, `AdsSettings`, `pending`, `MAX_CLIP_BYTES`, `MIN_BREAK_S`, `MAX_BREAK_S` (Task 3); `cue_ad_break`, `NotRunning`, `AdBreaks` (Task 5); `Targets.ads`, `Pipeline.last_check` (Task 4).
- Produces:
  - `ACTIONS` and `ROUTED_ACTIONS` gain `"ad-break"` and `"config"`. `refusals`: `"config"` is refused only while a job runs (`409 "<job> is still running"`); `"ad-break"` = `_first(busy, missing, Refusal(409, "Go live first: breaks are cued on a running channel.") unless channel RUNNING)`.
  - `Console` fields `config: Optional[ConfigStore] = None` and `ad_breaks: Optional[AdBreaks] = None`, built in `__post_init__` from `tf_dir`, `clock` and `clock_ms` when `None`.
  - `route(method, path, query, body, console, host="127.0.0.1", raw: bytes = b"", content_type: str = "")`.
  - `_pipeline_payload` adds `payload["ads"]`:
    ```
    {"saved": {"clip": bool, "clip_seconds": float|None, "duration_mode": str} | None,
     "clip_bytes": int|None, "deployed": dict|None, "pending": bool, "error": str|None,
     "trigger": {"mode": str, "seconds": float|None, "marker_only": bool} | None,
     "cue": AdBreaks.status() }
    ```
    `saved` is `None` and `error` set when the file is malformed; `trigger` is `None` when nothing is deployed or `targets.ads` is missing. It calls `ad_breaks.observe(pipeline.last_check)` first.
  - Routes: `GET /api/config` (the `ads` block alone); `POST /api/config/ads` `{duration_mode}`; `PUT /api/config/ads/clip?seconds=` (raw body); `DELETE /api/config/ads/clip`; `POST /api/ad-break` `{seconds?}`. `_Handler` gains `do_PUT` and `do_DELETE`.

- [ ] **Step 1: Write the failing tests.**
  - actions: `test_a_break_needs_a_running_channel` (`channel_state="IDLE"` → refusal text above); `test_config_changes_wait_for_a_job` (`job_running="deploy"` → 409); `test_config_is_allowed_off_air_and_without_a_stack`.
  - server (with `tf_dir=str(tmp_path)` and a runner whose outputs include `ads`):
    - `test_the_payload_reports_saved_and_deployed_ads`: save clip mode → `payload["ads"]["pending"] is True` against a deployed `per_trigger`.
    - `test_a_malformed_config_is_reported_not_overwritten`: file `"{"` → `ads.error` set, `POST /api/config/ads` → 409 and the file still reads `"{"`.
    - `test_uploading_a_clip_saves_it_with_its_length`: `PUT` with `raw=b"\0\0\0\x18ftypisom..."`, `content_type="video/mp4"`, `query={"seconds": ["24.5"]}` → 200, `ads.saved.clip is True`, `clip_seconds == 24.5`.
    - `test_an_upload_that_is_not_mp4_is_refused`: wrong content type → 400 `field == "clip"`; no `ftyp` → 400; missing `seconds` → 400 `field == "clip_seconds"`.
    - `test_an_oversized_upload_is_refused_unread` (Review Focus 4): drive `_Handler` with `Content-Length: 52428801` and an `rfile` that raises if read → 413, `close_connection` set.
    - `test_removing_the_clip`: `DELETE` → 200, file gone.
    - `test_a_cue_in_clip_mode_uses_the_deployed_length`: StubClient medialive RUNNING, deployed `{clip_md5:"x", clip_seconds:24.5, duration_mode:"clip"}` → `POST /api/ad-break {}` → 200 and `Duration == 2_205_000`.
    - `test_a_per_trigger_cue_needs_seconds_in_range`: `{}` → 400 `field == "seconds"`; `4` and `181` → 400; `30` → 200.
    - `test_a_second_cue_while_a_break_runs_is_refused` (Review Focus 3): two cues → second 409 mentions "s left"; one `batch_update_schedule` call.
    - `test_a_stack_without_the_ads_output_asks_for_a_redeploy` (Review Focus 1): outputs without `ads` → `POST /api/ad-break` 409 `"Deploy once more to enable ad breaks."`; `payload["ads"]["trigger"] is None`; `GET /api/config` still 200.
    - `test_a_medialive_error_is_shown_by_type`: `batch_update_schedule` raises → 502 with the type name, no AWS text.

- [ ] **Step 2: Run** `pytest -q tools/tests/test_actions.py tools/tests/test_server.py` — Expected: the new tests FAIL.

- [ ] **Step 3: Implement.** In `_Handler._dispatch`: when `Content-Length > MAX_CLIP_BYTES`, answer 413 without reading and set `close_connection`; for `PUT`, pass the bytes as `raw` and the `Content-Type` header, with `body = {}`. Copy for refusals: 413 `"The clip is larger than 50 MB."`; 400 `"Upload an MP4 file (H.264 video plays in every browser)."`.

- [ ] **Step 4: Run** `pytest -q tools` — Expected: PASS (the whole suite, since `route` changed signature).

- [ ] **Step 5: Commit** `feat: serve the ad settings, clip upload and break cue from the console`.

---

### Task 7: Configuration page with the Ads tab

**Files:**
- Create: `tools/livectl/site/config-page.js`
- Modify: `tools/livectl/site/index.html` (menu link `#config` "Configuration" after Source; `<main id="view-config" data-view="config">` with a `role="tablist"` bar holding one tab "Ads" and its `role="tabpanel"`), `tools/livectl/site/app.js` (`PAGES` gains `'config'`; `configPage.render(data)` and `.visible(...)` like `sourcePage`), `tools/livectl/site/api.js` (`PATHS['config-ads'] = 'config/ads'`, `uploadClip(file, seconds)` as `PUT config/ads/clip?seconds=` with `Content-Type: video/mp4`, `removeClip()` as `DELETE`), `tools/livectl/site/app.css`
- Modify: `tools/tests/scenarios.py`, `tools/tests/test_ui.py`
- Create: `tools/tests/fixtures/ad.mp4`: 2 s H.264 test clip, `ffmpeg -f lavfi -i testsrc2=size=320x180:rate=25 -t 2 -c:v libx264 -pix_fmt yuv420p -movflags +faststart tools/tests/fixtures/ad.mp4` (commit the file; it is under 50 KB).

**Interfaces:**
- Consumes: `payload.ads` and the routes from Task 6.
- Produces: `export class ConfigPage { constructor(root, act); render(data); visible(shown) }`. Scenario keys: `ads_saved: dict | "malformed"`, `ads_deployed: dict | None` (added to `OUTPUTS["ads"]`), `clip: bool` (writes the fixture to `tf_dir/ads/clip.mp4`). Each scenario gets its own `tempfile.mkdtemp()` as `tf_dir`.

- [ ] **Step 1: Add scenarios** `config-no-clip`, `config-clip-pending` (clip mode saved, deployed `per_trigger`), `config-clip-deployed`, `config-malformed`, and add them to `EXPECTED` in `test_ui.py` with the verdicts their stacks imply.

- [ ] **Step 2: Write the failing Playwright tests** (`open_scenario(name, view="config")`):
  - `test_the_ads_tab_offers_a_clip_when_there_is_none`: a file input accepting `video/mp4`; the "Length of the clip" radio is disabled; no "Saved — deploy to apply".
  - `test_picking_a_clip_uploads_it_with_its_length`: `set_input_files(fixture)` → preview shows `2.0 s` and the size; the scenario's `tf_dir/ads/clip.mp4` exists; the config file has `clip_seconds` ≈ 2.0.
  - `test_a_file_the_browser_cannot_play_is_refused_before_upload`: a text file renamed `.mp4` → an error under the picker; nothing written.
  - `test_a_saved_change_says_to_deploy`: `config-clip-pending` shows "Saved — deploy to apply".
  - `test_removing_the_clip_returns_to_per_trigger`: `config-clip-deployed` → click "Remove clip" → the picker is back and "Chosen per trigger" is checked.
  - `test_a_malformed_config_is_shown_instead_of_the_form`: `config-malformed` → the error text, no form controls.
  - `test_config_changes_are_disabled_while_a_job_runs`: the existing `deploying` scenario on `#config` → controls disabled, showing "deploy is still running".

- [ ] **Step 3: Run** `pytest -q tools/tests/test_ui.py -k "ads_tab or clip or config"` — Expected: FAIL.

- [ ] **Step 4: Implement** `ConfigPage`. Before upload, load the file through `URL.createObjectURL` into a detached `<video>` and wait for `loadedmetadata`; an `error` event, or a non-finite `duration`, rejects with "This file does not play in the browser; use an H.264 MP4." Re-render only when `payload.ads` changes (the `changed()` helper), so a poll never resets a half-made choice.

- [ ] **Step 5: Run** `pytest -q tools/tests/test_ui.py`, then `just ui-scenario config-no-clip` and click through by hand. Expected: PASS; the page works at 375 px width with no horizontal scroll.

- [ ] **Step 6: Commit** `feat: add a configuration page with an ads tab to the console`.

---

### Task 8: Cue control on the Live page

**Files:**
- Create: `tools/livectl/site/ad-break.js`
- Modify: `tools/livectl/site/index.html` (`<div class="ad-break" id="ad-break"></div>` after `#live-actions`), `tools/livectl/site/app.js` (call `renderAdBreak` in `render()`), `tools/livectl/site/api.js` (`PATHS['ad-break'] = 'ad-break'`), `tools/livectl/site/app.css`
- Modify: `tools/tests/scenarios.py`, `tools/tests/test_ui.py`

**Interfaces:**
- Consumes: `payload.ads.trigger`, `payload.ads.cue`, `payload.actions["ad-break"]`, `act('ad-break', {seconds})`.
- Produces: `export function renderAdBreak(box, ads, refusal, act)`. Scenario key `cue: dict` preloads `console.ad_breaks`, and `breaks: bool` makes the playlist fetch include a new DATERANGE.

- [ ] **Step 1: Add scenarios** `on-air-ads-clip` (deployed clip mode, 24.5 s), `on-air-ads-per-trigger`, `on-air-ads-marker-only` (no clip deployed), `ad-break-running` (cue 10 s ago, seen), `ad-break-not-seen` (cue 61 s ago, no DATERANGE), `on-air-old-stack` (no `ads` output); add each to `EXPECTED`.

- [ ] **Step 2: Write the failing Playwright tests** (`view="live"`):
  - `test_off_air_the_cue_says_why_not`: `off-air` → button disabled, text "Go live first: breaks are cued on a running channel."
  - `test_clip_mode_cues_the_clip_length`: `on-air-ads-clip` → no seconds field, button reads "Cue ad break · 24.5 s"; click → the stub medialive recorded `Duration == 2_205_000`.
  - `test_per_trigger_asks_for_seconds_and_checks_the_range`: field present, `4` → inline error naming 5–180, no call; `30` → one call.
  - `test_marker_only_is_labelled`: `on-air-ads-marker-only` → the line includes "marker only".
  - `test_a_running_break_shows_its_trace_and_blocks_another`: `ad-break-running` → "cued HH:MM:SS · in manifest HH:MM:SS · 30 s"; button disabled with the seconds left.
  - `test_an_unseen_marker_points_at_the_scte_settings`: `ad-break-not-seen` → the not-seen copy from Global Constraints.
  - `test_an_old_stack_asks_for_a_redeploy`: `on-air-old-stack` → "Deploy once more to enable ad breaks."

- [ ] **Step 3: Run** `pytest -q tools/tests/test_ui.py -k "cue or break or marker or old_stack"` — Expected: FAIL.

- [ ] **Step 4: Implement** `renderAdBreak`. Keep the seconds field's value and focus across polls (re-render only on change, as `steps.js` does).

- [ ] **Step 5: Run** `pytest -q tools/tests/test_ui.py`, then `just ui-scenario ad-break-running` and click through by hand — Expected: PASS.

- [ ] **Step 6: Commit** `feat: cue ad breaks from the live page and trace their markers`.

---

### Task 9: Player overlays the clip

**Files:**
- Modify: `modules/player/site/ads.js` (replace the placeholder), `modules/player/site/index.html`
- Test: `tools/tests/test_player_page.py`

**Interfaces:**
- Consumes: `config.json` `adClip` (Task 1); hls.js 1.5.15 `Hls.Events.LEVEL_UPDATED` with `data.details.dateRanges` (objects with `id`, `startDate: Date`, `duration`, `plannedDuration`, `attr['SCTE35-OUT']`) and `hls.playingDate: Date | null`.
- Produces: `window.AdBreaks = { breaks(dateRanges) -> [{id, startMs, endMs}], due(breaks, playingMs, played: Set) -> {id, startMs, endMs} | null }`. `breaks` keeps only ranges with `SCTE35-OUT` and a duration (`duration ?? plannedDuration`). `due` returns the first unplayed break with `startMs <= playingMs < endMs`.

- [ ] **Step 1: Extend the test harness.** `FAKE_HLS` gains `Hls.Events.LEVEL_UPDATED = 'hlsLevelUpdated'` and a settable `playingDate`. `_Site` serves `config.json` from a module-level dict the tests set (`adClip` on or off) and `/ads/clip.mp4` from `tools/tests/fixtures/ad.mp4`. The `<script src="ads.js">` loads from the site directory as is.

- [ ] **Step 2: Write the failing tests.**
  - `test_breaks_keeps_scte35_out_ranges_with_a_duration` and `test_due_finds_the_break_under_the_playhead` (call `window.AdBreaks` through `page.evaluate`): one out, one without `SCTE35-OUT`, one without any duration → one break; `due` before, inside and after the window → `null`, the break, `null`; with its id in `played` → `null`.
  - `test_a_break_overlays_the_clip_and_mutes_live`: emit `LEVEL_UPDATED` with a 2 s break starting now, set `playingDate` inside it → `#ad` visible, the live `<video>` `muted === true` and not paused.
  - `test_the_break_ends_on_time_and_live_is_unmuted`: move `playingDate` past `endMs` → `#ad` hidden and paused; live `muted` back to its value before the break.
  - `test_a_short_clip_loops_through_a_long_break`: break 6 s, fixture 2 s → `#ad.loop === true` while it runs.
  - `test_a_break_plays_once` (Review Focus 5): end a break, emit the same range again with `playingDate` inside it → overlay stays hidden.
  - `test_joining_mid_break_shows_the_rest` (Review Focus 5): first `playingDate` halfway through → overlay shown, hidden at `endMs`.
  - `test_without_a_clip_live_stays_on_screen`: `adClip: null` → no `#ad` element; live never muted.
  - `test_a_clip_that_fails_to_load_skips_the_break`: `adClip` pointing at a 404 → the overlay ends and live is unmuted.

- [ ] **Step 3: Run** `pytest -q tools/tests/test_player_page.py` — Expected: the new tests FAIL.

- [ ] **Step 4: Implement.** In `index.html`, load `<script src="ads.js"></script>` after hls.js; after `config.json` loads: if `adClip`, create `<video id="ad" preload="auto" playsinline loop hidden>` over `#video`, listen to `LEVEL_UPDATED` and merge `AdBreaks.breaks(...)` by id; every 250 ms take `hls.playingDate`, start a break with `AdBreaks.due`, and end it when `playingDate >= endMs` or on the clip's `error`. Keep the live video playing throughout; only `muted` changes.

- [ ] **Step 5: Run** `pytest -q tools/tests/test_player_page.py && terraform -chdir=modules/player test` — Expected: PASS.

- [ ] **Step 6: Commit** `feat: overlay the ad clip in the player for each scte-35 break`.

---

### Task 10: Documentation and the full suite

**Files:**
- Modify: `README.md` (an "Ad breaks" section: what the button does, CSAI vs SSAI in three sentences, where the clip and settings live), `AGENTS.md` (*Secrets*: `*.auto.tfvars.json` and `envs/demo/ads/` are gitignored; *Python conventions*: `config.py` owns only its keys in the console's tfvars file), `docs/cost-estimate.md` (one line: cues are free; clip delivery is CloudFront egress at a few MB per view)

- [ ] **Step 1: Write the docs.** Keep the one claim that is still unverified honest: "MediaPackage v2's need for `ad_markers` in addition to TS passthrough is unverified until the first live run."
- [ ] **Step 2: Run** `just fmt && just test` — Expected: everything passes; `git status` shows no `console.auto.tfvars.json` or `ads/` from test runs.
- [ ] **Step 3: Commit** `docs: explain ad breaks, where their settings live and what they cost`.
