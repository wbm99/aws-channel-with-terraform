# Source settings: Design

Date: 2026-10-07
Status: Approved; planned in `docs/superpowers/plans/2026-10-07-plan-9-source-settings.md`
Branch: `feat/source-settings` (from `main`)

This document has two parts: a product part (PRD: what and why) and a technical part (TRD: how).

It is sub-project A of two. Sub-project B, playing a file as the source instead of a test pattern, comes later on
`feat/source-file`, stacked on this branch. There the file becomes one more kind of input on the Source page; the test
patterns stay.

---

# Part 1: PRD

## Purpose
Let the person running the demo shape the SRT contribution feed instead of accepting one fixed encode. They can change
the picture, the audio and the transport stream identity, and see what MediaConnect actually received. Each choice
becomes something to try and explain: frame-rate conversion, bitrate against the channel's input class, the TS service
name surviving the SRT hop.

## Audience
The author, demonstrating and writing about the pipeline, and a reviewer running the console on their own account.

## Goals
1. A **Source** page in the console holds every setting of the FFmpeg test source, grouped as Video, Audio, MPEG-TS and
   SRT.
2. You edit several fields, then one **Apply** restarts FFmpeg once with all of them. While the source is stopped, the
   same button reads **Send test source**.
3. Every choice stays inside what the deployed MediaLive channel declares (`AVC`, `HD`, `MAX_10_MBPS`): no setting
   offered by the console can push the feed outside the channel's input class.
4. A **Sent vs received** panel puts the settings FFmpeg is using next to what MediaConnect parsed from the incoming
   transport stream.
5. `just send` accepts the same settings as environment variables. The script stays the single source of truth for
   how a setting becomes an FFmpeg argument.
6. Restarting the source is reliable. The known race in `SourceProcess` (an old reader thread overwriting the new
   process's state and clearing its redaction secret) is fixed.

## Success criteria
- Changing the frame rate and the service name, then pressing Apply once, restarts FFmpeg once, and the readback shows
  the new frame rate and `ProgramName` within a few seconds (live check).
- A value outside its range is refused by the API with a message naming the field, and the page shows it under that
  field.
- `SERVICE_NAME="Match 1" FPS=25 just send` produces a feed with that service name at 25 fps.
- The Control page still runs deploy → go live → send without visiting the Source page.
- All new behaviour is covered by the offline suite: unit, server, script and browser tests.

## Non-goals
- **HEVC.** The channel declares `codec = "AVC"`. HEVC is a follow-up: an `input_codec` variable on `modules/encode`,
  a Terraform output the console reads to enable the option, and the HEVC input price in `docs/cost-estimate.md`.
  Before that, check the MediaLive docs for what happens when the stream does not match the declared input.
- File playback (sub-project B).
- x264 preset and H.264 profile.
- Named presets ("Contribution 1080p30", …). They could be added on top later as quick-fill buttons.
- Remembering settings across console restarts.

## Settings

| Group | Field | Choices | Default | Note |
|---|---|---|---|---|
| Video | Pattern | testcard, smpte, pal, black, standby | testcard | unchanged list |
| | Resolution | 1280×720, 1920×1080 | 1920×1080 | the channel's `HD` input class |
| | Frame rate | 25, 30, 50, 60 | 30 | the output is fixed at 30 fps, so 25 and 50 show frame-rate conversion |
| | Bitrate | 500 to 10000 kbps | 6000 kbps | capped by `MAX_10_MBPS`; the VBV buffer is 2× the bitrate |
| | Keyframe interval | 1, 2, 4 s | 2 s | GOP in frames = fps × seconds |
| Audio | Codec | AAC, MP2, AC-3 | AAC | MediaLive re-encodes to AAC; this tests what its input accepts |
| | Bitrate | 64, 96, 128, 192, 256 kbps | 128 kbps | |
| | Tone | 440 Hz, 1 kHz, silence | 1 kHz | |
| MPEG-TS | Service name | 1–60 printable characters | `livectl test source` | FFmpeg `-metadata service_name` |
| | Provider | 1–60 printable characters | `livectl` | FFmpeg `-metadata service_provider` |
| | Program number | 1–65535 | 1 | FFmpeg `-mpegts_service_id` |
| SRT | Latency | 20–8000 ms | 120 ms | the larger of this and the flow's `min_latency` applies |

"Printable characters" means no control characters. Spaces, quotes and non-ASCII letters are allowed.

Two changes to current behaviour come with this: audio becomes **stereo at 48 kHz** instead of FFmpeg's mono 44.1 kHz
default for the `sine` generator, and the audio encoder gets an explicit bitrate. The VBV buffer follows the bitrate
instead of the fixed `12M`.

## Checked in the AWS documentation (2026-10-07)
1. MediaLive's MediaConnect, SRT caller and SRT listener inputs accept AAC, Dolby Digital (AC-3), Dolby Digital Plus,
   MPEG Audio (MP2), Dolby E in PCM and PCM
   ([Supported codecs by input type](https://docs.aws.amazon.com/medialive/latest/ug/inputs-supported-codecs-by-input-type.html)).
   All three audio codecs stay. FFmpeg encodes each at 64 and 256 kbps stereo (`test_send_script.py`).
2. MediaConnect's program name "is sourced from the service name value in the Service Description Table (SDT)"
   ([Monitoring using source metadata](https://docs.aws.amazon.com/mediaconnect/latest/ug/monitor-with-source-stream-monitoring.html)).
   The readback row compares it with the service name sent.

## Changes made while implementing
- The pipeline payload also carries `source_settings.defaults`, so *Reset to defaults* needs no values in the page.
- The Source page polls `/api/source/received` whenever it is visible. The server answers `idle` without calling
  MediaConnect unless the flow is `ACTIVE`, so this costs no AWS call while off air.
- Browser scenarios: `source-stopped` and `source-refused` are the existing `on-air-no-source` and `off-air`;
  `source-dirty` is reached by editing in the test; `source-running` gained matching metadata and
  `source-received-mismatch` is new.
- Disabled console buttons now look disabled (`button.act:disabled`); before this page, unavailable buttons were
  always hidden.

---

# Part 2: TRD

## Units

| Unit | Purpose | Depends on |
|---|---|---|
| `livectl/source.py` | `SourceSettings`, `CHOICES`, `SettingsError`; `SourceProcess` takes settings and restarts safely | `subprocess` (injected popen) |
| `livectl/received.py` (new) | normalise `DescribeFlowSourceMetadata` | an injected MediaConnect client |
| `livectl/server.py` | hold current settings; settings, start and received endpoints | the two above |
| `source/send-srt.sh` | turn environment variables into FFmpeg arguments, validating each | FFmpeg |
| `site/source-page.js` (new) | the Source page: form, unsaved changes, Apply, sent vs received | `api.js`, the pipeline payload |
| `site/steps.js`, `site/app.js` | Control page summary line and Edit link; pattern picker removed | |

## `source.py`

```python
@dataclass(frozen=True)
class SourceSettings:
    pattern: str = "testcard"
    size: str = "1920x1080"
    fps: int = 30
    video_kbps: int = 6000
    gop_seconds: int = 2
    audio_codec: str = "aac"
    audio_kbps: int = 128
    tone: str = "1000"          # "440", "1000" or "silence"
    service_name: str = "livectl test source"
    service_provider: str = "livectl"
    program_number: int = 1
    latency_ms: int = 120

    def merged(self, partial: Mapping[str, object]) -> "SourceSettings": ...
    def to_env(self) -> dict[str, str]: ...
    def to_dict(self) -> dict: ...
```

- `CHOICES` describes every field in display order: `group`, `label`, `kind` (`enum`, `range` or `text`), `values`
  with labels (enum), `min`/`max`/`step`/`unit` (range), `max_length` (text). It is the only place the allowed values
  live. `PATTERNS` becomes the enum values of `pattern`.
- `merged()` rejects unknown keys and wrong types, and checks each value against `CHOICES`. It raises
  `SettingsError(field, message)` (a `ValueError`) on the first bad field. A partial dict changes only the keys it
  holds.
- `to_env()` returns `PATTERN`, `SIZE`, `FPS`, `BITRATE` (as `<n>k`), `GOP_SECONDS`, `AUDIO_CODEC`, `AUDIO_BITRATE`
  (as `<n>k`), `TONE`, `SERVICE_NAME`, `SERVICE_PROVIDER`, `PROGRAM_NUMBER`, `SRT_LATENCY_MS`.

`SourceProcess` changes:

- `start(*, host, port, passphrase, settings: SourceSettings)` replaces `pattern=`. The environment is
  `os.environ | settings.to_env() | {SRT_HOST, SRT_PORT, SRT_PASSPHRASE}`.
- **Generation guard.** Each start increments `self._generation`, and the reader thread is passed its generation, the
  process and that process's secret. The reader redacts with its own secret, and updates `_state`, `_exit_code`,
  `_progress` and `_lines` only while its generation is current. A reader that finishes after a newer start (because
  `stop()`'s join timed out) changes nothing.
- `restart(*, host, port, passphrase, settings)` stops and starts under a dedicated restart lock, so two Apply requests
  arriving together run one after the other instead of interleaving.
- `status()` adds `settings`: the `to_dict()` of the settings the current or last process was started with. `pattern`
  is removed (it is inside `settings`).

## `received.py`

```python
def received(mediaconnect, flow_arn: str) -> dict:
    """What MediaConnect parsed from the incoming transport stream."""
```

It returns `{"programs": [{"name", "number", "streams": [{"type", "codec", "width", "height", "fps", "channels",
"sample_rate"}]}], "messages": [str], "at": <ms>}`. A missing field becomes `None`. No programs and no messages means
nothing has been parsed yet.

## Server

- `Console` gains `settings: SourceSettings` (the defaults at launch) and a lock around reading and replacing it.
- `GET /api/pipeline`: `patterns` is replaced by `source_settings: {"current": ..., "choices": ...}`. `source` carries
  `settings` from `status()`.
- `POST /api/source/settings`, body = changed fields only:
  - invalid: `400 {"error": message, "field": name}`;
  - valid, source stopped: save, `200 {"saved": true, "current": ...}`;
  - valid, source running: save, `restart()`, then `202 {"saved": true, "restarted": true, "current": ...}`. The
    passphrase and target checks of today's `_start_source` apply, with the same messages.
- `POST /api/source/start`: no body, uses `console.settings`. The refusals are unchanged.
- `POST /api/source/pattern`: removed. It answers 404 like any unknown path.
- `GET /api/source/received`:
  - flow not deployed or not `ACTIVE`: `200 {"state": "idle", "note": "The flow is not active."}`;
  - otherwise: `200 {"state": "ok", ...received()}`;
  - an AWS error: `200 {"state": "error", "note": "Could not read the source metadata (<ErrorType>)."}`. The error
    body is never echoed.
  - It is a separate endpoint, so the 2 s pipeline poll never calls MediaConnect for it.

## `source/send-srt.sh`

- New optional variables: `FPS` (default 30), `GOP_SECONDS` (2), `AUDIO_CODEC` (aac), `AUDIO_BITRATE` (128k), `TONE`
  (1000), `SERVICE_NAME`, `SERVICE_PROVIDER`, `PROGRAM_NUMBER` (1). `SIZE`, `BITRATE`, `PATTERN` and
  `SRT_LATENCY_MS` keep their meaning, and `SRT_LATENCY_MS` now defaults to 120.
- `BITRATE` accepts a whole number followed by `k` or `M`. It is normalised to kbps, and `-bufsize` is 2× that.
- Each variable is checked against the same allowed values as `CHOICES`, with `error: unknown FPS '24' (use one of:
  25, 30, 50, 60)`-style messages and exit 1. The comment at the top of the script lists the variables and points at
  `CHOICES` as the list to keep in step with, as it already does for `PATTERNS`.
- The FFmpeg command is built as a bash array, so values with spaces or quotes stay single arguments.
- Mapping: `rate=$FPS` in every lavfi generator; `-g`/`-keyint_min` = FPS × GOP_SECONDS; audio from
  `sine=frequency=$TONE:sample_rate=48000` or `anullsrc=r=48000:cl=stereo` for silence, then `-ac 2 -ar 48000`;
  `-c:a aac|mp2|ac3 -b:a $AUDIO_BITRATE`; `-metadata service_name=... -metadata service_provider=...
  -mpegts_service_id $PROGRAM_NUMBER`.
- `--frame` keeps working and honours `PATTERN` and `SIZE`.

## Frontend

- Navigation becomes Control · Source · Live · Logs. The Source page is `#source`.
- `source-page.js` builds the form from `choices`: enum → `<select>`, range → `<input type=number>` with min/max/step
  and a unit label (bitrate is shown in Mbps with step 0.1 and converted to kbps), text → `<input maxlength>`. The
  groups are four fieldsets. Each field has a `name` equal to its settings key, which is what the browser tests target.
- **Unsaved changes.** The form keeps the server `current` it was built from. A field that differs gets a marker and an
  accessible "changed" label. The main button shows the count. The poll never replaces a focused or changed field. If
  `current` changes on the server (another tab) and the form has no changes, the form takes the new values.
- **Buttons.** The main button reads:
  - **Send test source** when stopped (it posts the changes, then starts);
  - **Apply N changes** when running with changes;
  - a disabled **Apply** when running with none.

  There are also **Discard** (back to `current`), **Stop**, and **Reset to defaults** (fills the form with the
  defaults from `choices`; nothing is sent until Apply). When `source-start` is refused, the button is disabled and
  shows the same refusal message the Control page uses.
- **After Apply** (202): "Restarting FFmpeg — SRT reconnects, expect a few seconds of slate." On a 400: the message
  under the named field, and that field is focused.
- **Sent vs received.** Rows for video (codec, resolution, fps), audio (codec, channels, sample rate) and program
  (name, number). "Sent" is from `source.settings`, "received" from `/api/source/received`, polled every 5 s only while
  the Source page is visible and the source is running or the SRT node is connected. A row whose values disagree is
  highlighted. An idle or error answer shows its `note` in place of the table. A received codec name is compared after
  normalising (`h264` = AVC, `aac`, `mp2`/`mp1`, `ac3`).
- **Control page.** The source step drops the pattern picker and keeps Send/Stop, plus one summary line built from
  `current`, e.g. `Test card · 1080p30 · 6 Mbps · AAC 128k · "livectl test source" — Edit on Source`. The link goes to
  `#source`.
- On a narrow window the two columns stack.

## Testing

All offline, with no credentials.

- `tools/tests/test_source.py`:
  - settings defaults; `merged()` accepting every enum value and both ends of every range;
  - `merged()` refusing 499 and 10001 kbps, program 0 and 65536, a 61-character name, a control character, an unknown
    key and a wrong type, each naming the field;
  - `to_env()`;
  - the generation guard, using a fake popen whose old reader finishes after the new start: the state stays
    `running`, the new passphrase is still redacted, and the old exit code is not reported;
  - two concurrent `restart()` calls produce two sequential restarts.
- `tools/tests/test_received.py`: full, empty and messages-only stub answers.
  `tools/tests/stubs.py` gains `describe_flow_source_metadata`.
- `tools/tests/test_server.py`:
  - settings saved while stopped (no FFmpeg started);
  - settings applied while running (exactly one restart, with the merged settings);
  - a 400 with `field`;
  - start using the saved settings;
  - `/api/source/pattern` returning 404;
  - `/received` idle, ok and error.
- `tools/tests/test_send_script.py` (new): runs `source/send-srt.sh` with a fake `ffmpeg` first on `PATH` that prints
  its arguments one per line. It asserts `-g 50` for FPS=25 and GOP_SECONDS=2, a `-bufsize` of 2× the bitrate, a
  service name with spaces arriving as one argument, `anullsrc` for silence, and exit 1 with a message for each bad
  value. No real encode.
- `tools/tests/scenarios.py`: states `source-stopped`, `source-running`, `source-dirty`, `source-received-mismatch`
  and `source-refused`. `just test-ui`:
  - edit two fields → the button reads "Apply 2 changes" → click → the request body holds exactly those two fields;
  - a 400 shows under the right field;
  - Discard restores the values;
  - the mismatch row is highlighted;
  - the refused state disables Send with its message;
  - the Control page shows the summary line, and the Edit link opens `#source`.
- Every state is also clicked through by hand before the work is called done, not just screenshotted.

## Docs

- `README.md`: the Source page; the new `just send` variables; additions to *Verify the console live*:
  1. press Apply while the player is playing, and note what the player, the slate, the SRT node and the next-step hint
     each do (an earlier restart misbehaved, details not recorded);
  2. 25 and 50 fps play, converted to 30;
  3. MP2 and AC-3 audio are accepted (if kept after the docs check);
  4. the service name appears as `ProgramName` in the readback.
- `AGENTS.md`: one line under the Python conventions: `SourceSettings`/`CHOICES` is where the allowed source values
  live, and `send-srt.sh` mirrors them.
