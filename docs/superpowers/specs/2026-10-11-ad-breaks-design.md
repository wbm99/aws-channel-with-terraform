# Ad breaks with SCTE-35: Design

Date: 2026-10-11
Status: Draft, for review
Branch: `feat/ad-breaks` (from `main`)

This document has two parts: a product part (PRD: what and why) and a technical part (TRD: how).

It also opens a **Configuration** page in the console. Ads is its first and only tab; later tabs will hold settings
for other resources, following the pattern set here.

---

# Part 1: PRD

## Purpose
Show how a live channel signals an ad break, end to end: an operator cues a break, MediaLive writes a SCTE-35 splice
insert into the stream, MediaPackage turns it into an `EXT-X-DATERANGE` tag in the HLS manifest, and the player reacts
to that tag by playing an ad clip over the live picture for the break's duration.

## Audience
The author, running and writing about the pipeline, and a reviewer running the stack on their own account.

## Goals
1. A **Configuration → Ads** tab in the console where the operator can, before deploying, optionally add an ad clip
   (an MP4 they supply) and choose how break durations are set.
2. The settings reach AWS through Terraform: the console writes them to a file Terraform reads, and a deploy (from the
   console or `just up`) uploads the clip with the player.
3. A **Cue ad break** control on the Live page, enabled only while on air.
4. Two duration modes, chosen in the Ads tab: **the clip's length**, or **chosen per trigger** (5–180 s).
5. The player plays the clip for exactly the break's duration: it loops a clip shorter than the break and cuts one that
   is longer, then returns to live with no visible buffering.
6. Without a clip, the trigger still works as **marker only**: the break appears in the manifest and the console, and
   the player stays on live.
7. The console traces each cue: when it was sent, when the marker appeared in the manifest, and its duration.

## Success criteria
- `just test` passes offline, including every new state of the Configuration page and the Live page's ad control.
- In one live run: a cue appears as `EXT-X-DATERANGE` in the manifest; the clip plays over live for the break's
  duration in both modes; live returns cleanly; the marker-only case leaves the player on live.
- A deploy with no `ads` settings behaves exactly as today.

## Non-goals
- **Server-side ad insertion** (MediaTailor and an ad decision server). This design is client-side insertion (CSAI):
  the clip is a separate file, visible in developer tools and blockable by an ad blocker. SSAI is the natural next
  step and the reason broadcasters avoid CSAI; the design stays honest about that rather than hiding it.
- VAST, ad decision servers, multiple clips, ad rotation, targeting, or tracking beacons.
- Scheduled breaks (at a time of day, or on a timer). Breaks are cued by hand, immediately.
- Swapping the clip while live without a deploy.
- Settings for other resources. The Configuration page is built to take more tabs; none are added now.
- A database. Deployment configuration is a file Terraform reads, as is usual for configuration as code. A database
  would add a dependency (breaking the boto3-only rule), a container and migrations, and would still need rendering
  into Terraform variables.

## Decisions taken while brainstorming
| Question | Decision |
|---|---|
| Where the ad comes from | A clip the operator supplies, uploaded through the console. Optional. |
| How it reaches S3 | Through Terraform, as an `aws_s3_object` in the player bucket. |
| Where settings are stored | `envs/demo/console.auto.tfvars.json`, owned by the console, read natively by Terraform. |
| Break duration | A per-stack mode: the clip's length, or chosen per trigger. |
| Break vs clip length | Loop the clip if the break is longer; cut it if shorter. The break decides when live returns. |
| No clip deployed | The trigger sends the marker only. |
| Where the trigger lives | The Live page, under the player. |
| How the player swaps | The clip plays in an overlay over the live stream, which keeps playing muted underneath. |

---

# Part 2: TRD

## Data flow

```
 Configuration → Ads tab          envs/demo/console.auto.tfvars.json   (gitignored)
  pick clip, duration mode  ───▶  { "ads": { "clip": "ads/clip.mp4",
  browser reads clip length          "clip_seconds": 24.5, "duration_mode": "clip" } }
  clip saved to envs/demo/ads/ ──▶ envs/demo/ads/clip.mp4               (gitignored)
                                        │ Deploy (console or `just up`)
                                        ▼
  player module: aws_s3_object for the clip (only when set) + `ad_clip` in config.json
  encode module: SCTE-35 passthrough in TS + ELEMENTAL_SCTE35 ad markers (always on)
  package module: scte_filter [SPLICE_INSERT], scte_hls DATERANGE         (always on)
  output `ads` ──▶ livectl reads it through targets, like the channel ID

 Live page ▸ "Cue ad break" (on air only; seconds field when per trigger or no clip)
   POST /api/ad-break ──▶ control.cue_ad_break(medialive, channel_id, seconds, ...)
   BatchUpdateSchedule: immediate Scte35SpliceInsert, Duration = seconds × 90 000
        │
        ▼  MediaLive TS ──▶ MediaPackage ──▶ manifest: EXT-X-DATERANGE SCTE35-OUT, DURATION
                                              │
  console reads the manifest ─▶ "cued 12:03:10 · in manifest 12:03:16 · 30 s"
  player (hls.js 1.5.15) reads dateRanges ─▶ clip set? overlay the clip, live muted below,
                                            loop or cut at DURATION, then unmute live
                                            no clip? stay on live (marker only)
```

The SCTE-35 settings in encode and package are **always on**, with no variable: they cost nothing and change nothing
until a cue arrives. The clip is the only optional part.

## Checked offline against the installed schemas (2026-10-11)
- `aws` 6.65.0, `aws_medialive_channel`: `m3u8_settings` has `scte35_behavior` (`PASSTHROUGH` | `NO_PASSTHROUGH`) and
  `scte35_pid`; `hls_group_settings` has `ad_markers` (`ADOBE` | `ELEMENTAL` | `ELEMENTAL_SCTE35`).
- `awscc` 1.102.0, `awscc_mediapackagev2_origin_endpoint`: `segment.scte.scte_filter` ("the SCTE-35 message types
  that you want to be treated as ad markers in the output") and `hls_manifests[].scte_hls.ad_marker_hls`.
- boto3 MediaLive model: `Scte35SpliceInsertScheduleActionSettings` takes `SpliceEventId` (required, 0–2³²−1) and
  `Duration` (0–2³³−1, in **90 kHz ticks**). `ImmediateModeScheduleActionStartSettings` has no members.
- CloudFront (`modules/delivery`): only `/out/*` goes to MediaPackage; the default behavior serves the player bucket,
  so `/ads/clip.mp4` needs no new behavior. The default behavior uses the caching-disabled policy; at a few MB per
  view that is acceptable for a demo.

**Not verifiable offline:** whether MediaPackage v2 reads SCTE-35 from the TS segments alone or also needs MediaLive's
`ad_markers` in the ingest manifests. The design sets both, which is harmless. This is the item most likely to need a
fix after the first live run (see *Lessons learned* in `README.md` for the precedent).

## Terraform

### `modules/player`
The player module owns the clip and `config.json`, so the `ads` variable and its validation live here.
`envs/demo` has no test suite; keeping validation in the module puts it under the existing `tests/player.tftest.hcl`.

- New variable `ads`:
  ```hcl
  variable "ads" {
    description = "Ad break settings from the console: an optional MP4 clip, its length, and how break durations are set."
    type = object({
      clip          = optional(string)
      clip_seconds  = optional(number)
      duration_mode = optional(string, "per_trigger")
    })
    default = {}
  }
  ```
  Validation rules, each with its own message:
  1. `duration_mode` is `clip` or `per_trigger`.
  2. `clip`, when set, ends in `.mp4` and `fileexists(clip)`.
  3. `clip_seconds > 0` when `clip` is set.
  4. `duration_mode = "clip"` requires `clip`.
- `aws_s3_object.ad_clip`, `count = var.ads.clip == null ? 0 : 1`, key `ads/clip.mp4`, `content_type = "video/mp4"`,
  `etag = filemd5(var.ads.clip)` so a new clip re-uploads.
- `config.json` gains `"ad_clip": "/ads/clip.mp4"` or `null`.
- New output `ads`: the deployed `{ clip_present, clip_seconds, duration_mode }`.

### `envs/demo`
- New variable `ads` (same type, no validation, `default = {}`), passed to the player module with `clip` resolved
  relative to `envs/demo` (`"${path.root}/${var.ads.clip}"`).
- New output `ads` re-exporting the player module's.

### `modules/encode`
- In `m3u8_settings`: `scte35_behavior = "PASSTHROUGH"`. This also ends the empty `m3u8_settings {}` block.
- In `hls_group_settings`: `ad_markers = ["ELEMENTAL_SCTE35"]`.

### `modules/package`
- On the origin endpoint: `segment = { scte = { scte_filter = ["SPLICE_INSERT"] } }`.
- On the `index` HLS manifest: `scte_hls = { ad_marker_hls = "DATERANGE" }`.

### `.gitignore`
Add `envs/demo/*.auto.tfvars.json` and `envs/demo/ads/`. The existing `*.tfvars` does not match
`*.auto.tfvars.json`.

## livectl

### `livectl/config.py` (new)
Reads and writes `console.auto.tfvars.json` in the Terraform directory.
- Owns only the keys it knows (`ads` for now). Other keys in the file are kept unchanged.
- Writes are atomic (temporary file, then rename).
- A malformed file raises `ConfigError`; the console then refuses to save rather than overwrite it.
- `AdsSettings`: a frozen dataclass with `clip`, `clip_seconds`, `duration_mode`, and validation mirroring Terraform's,
  so the console rejects bad input before a deploy does.
- `pending(saved, deployed)`: whether the saved settings differ from the `ads` output, which drives "Saved — deploy to
  apply".

### `control.cue_ad_break(medialive, channel_id, seconds, *, clock)`
- Refuses unless the channel is `RUNNING`.
- Sends one `BatchUpdateSchedule` with a `Creates.ScheduleActions` entry: `ActionName` `ad-break-<event id>`,
  immediate start, `Scte35SpliceInsertSettings` with `SpliceEventId` derived from the injected clock (seconds modulo
  2³²) and `Duration = round(seconds × 90000)`.
- Returns a frozen `AdBreak(event_id, seconds, cued_at)`.

### `manifest.py`
Detects `EXT-X-DATERANGE` tags with `SCTE35-OUT` in a variant manifest and returns their ID, start and duration
(`DURATION`, else `PLANNED-DURATION`).

### `server.py`
| Route | Purpose |
|---|---|
| `GET /api/config` | Saved and deployed `ads`, `pending`, and a config error if the file is malformed. |
| `POST /api/config/ads` | Save `duration_mode` (and `clip_seconds` from the browser). 400 with `field` on invalid input. |
| `PUT /api/config/ads/clip` | Raw MP4 body (no multipart; the stdlib `cgi` module is gone in Python 3.13). `Content-Type: video/mp4` required, at most 50 MB, saved atomically to `envs/demo/ads/clip.mp4`. |
| `DELETE /api/config/ads/clip` | Remove the clip and clear `clip` and `clip_seconds`; a `clip` mode falls back to `per_trigger`. |
| `POST /api/ad-break` | Cue a break with `seconds`, which comes from the deployed `clip_seconds` in `clip` mode or from the body (5–180) otherwise. |

- Config changes are refused while a job runs, through `actions.refusals`, like other actions.
- A cue is refused while the previous break is still running (`cued_at + seconds > clock()`), so a double-click
  cannot stack two breaks.
- The last cue is kept in memory. The polled payload reports it with the time its marker was first seen in the
  manifest, or `not_seen` after 60 s ("not seen in manifest after 60 s — check SCTE settings in package/encode").

## Console UI
- **Configuration page** (`#config`) with a sub-tab bar; **Ads** is its only tab. A new resource adds a tab, not a page.
- **Ads tab:**
  - The clip picker loads the file into a hidden `<video>` first: an unplayable file is rejected before upload, and
    `duration` comes from the browser.
  - A preview with the clip's name, length and size, and **Remove clip**.
  - The duration mode radio; `clip` is disabled when no clip is saved.
  - "Saved — deploy to apply" while `pending`; the config error, if any, in place of the form.
- **Live page**, under the player:
  - A **Cue ad break** button, enabled only on air, otherwise showing the reason.
  - A seconds field (5–180) when the mode is per trigger or there is no clip.
  - A status line: "cued 12:03:10 · in manifest 12:03:16 · 30 s", with "· marker only" when no clip is deployed.

## Player (`modules/player/site/index.html`)
- Reads `ad_clip` from `config.json`. When set, creates an overlay `<video>` with `preload="auto"`, so the cut into the
  ad is instant.
- On `LEVEL_UPDATED`, collects new `dateRanges` that carry `SCTE35-OUT`.
- When playback reaches a break's start: show the overlay, play the clip with `loop`, and mute live, which keeps
  playing underneath. At the break's duration: hide the overlay, pause the clip and unmute live, which is already at
  the right point (no seek, no buffering).
- Each event ID plays once, so a manifest refresh or a reload never replays a break.
- If the clip fails to load, the break is skipped and live continues. With no `DURATION` and no `PLANNED-DURATION`,
  the break is ignored.
- The scheduling (which break, when to start, when to end) is a pure function, separate from the DOM, so it can be
  tested with fake `dateRanges`.

## Testing
All offline, under `just test`.

- **terraform test:**
  - `modules/player`: no clip (no object, `ad_clip: null`), with a clip (one object, the right key, content type and
    `config.json`), and each validation rule, passing and failing (`expect_failures`).
  - `modules/encode`: `scte35_behavior` and `ad_markers`.
  - `modules/package`: `scte_filter` and `ad_marker_hls`.
- **pytest:**
  - `config.py`: round trip, other keys kept, malformed file, atomic write, `pending`.
  - `cue_ad_break`: the 90 kHz conversion, the event ID from the clock, refusal when not running. Through
    `tests/stubs.py` if moto has no schedule API.
  - `manifest.py`: `DATERANGE` detection on fixture manifests, including `PLANNED-DURATION` and no duration.
  - Server routes: size limit, content type, refusals during a job, the double-cue guard, `not_seen` after 60 s.
- **Playwright** (`just test-ui`), clicking through every state:
  - Configuration: no clip, clip saved but not deployed, deployed, malformed file, upload rejected.
  - Live page: off air, on air with a clip, on air marker only, per trigger, break in progress, marker not seen.
- **Player logic:** the scheduling function in the browser with fake `dateRanges`: start, loop, cut, one play per
  event, missing duration.

## Live verification
Only when the user asks in a session. One run of about 30 minutes (about 1 USD), with this checklist:
1. A cue in `clip` mode appears as `EXT-X-DATERANGE` within about 30 s; the clip plays for its length; live returns
   with no buffering.
2. A per-trigger break longer than the clip loops it; a shorter one cuts it.
3. After removing the clip and redeploying, a cue reaches the manifest and the player stays on live.
4. `just stop`, `just down`, `just check-clean`.

Findings go into `docs/study-notes.md`, and any rejection by AWS into *Lessons learned* in `README.md`.
