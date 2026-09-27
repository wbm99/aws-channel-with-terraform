#!/usr/bin/env bash
# Push a test stream with a burned-in UTC clock to an SRT listener.
#
# Required environment:
#   SRT_HOST        ingest IP or hostname of the MediaConnect flow (see: livectl status)
#   SRT_PASSPHRASE  SRT passphrase (from Secrets Manager; never put it on the command line)
# Optional environment:
#   SRT_PORT        listener port (default 5000)
#   SRT_LATENCY_MS  SRT receiver latency in milliseconds; FFmpeg expects microseconds, converted here
#   SIZE            video size (default 1920x1080)
#   BITRATE         video bitrate (default 6M)
#   FONT            TrueType font for the clock (default DejaVu Sans)
#   PATTERN         test pattern: testcard (default), smpte, pal, black, standby
# Local check without any network:
#   source/send-srt.sh --frame /tmp/clock.png

set -euo pipefail

SIZE="${SIZE:-1920x1080}"
BITRATE="${BITRATE:-6M}"
FONT="${FONT:-/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf}"

if [[ ! -f "$FONT" ]]; then
  echo "error: font not found: $FONT (install fonts-dejavu-core or set FONT)" >&2
  exit 1
fi

# %T is HH:MM:SS, so the only colon that needs escaping is the one after gmtime.
CLOCK="drawtext=fontfile=${FONT}:text='%{gmtime\:%T}':fontsize=96:fontcolor=white:box=1:boxcolor=black@0.6:x=40:y=40"
# Every pattern is an FFmpeg built-in generator; the UTC clock is burned in on top of all of them.
# The livectl console offers the same list (tools/livectl/source.py PATTERNS); keep the two in step.
PATTERN="${PATTERN:-testcard}"
case "$PATTERN" in
  testcard) SOURCE="testsrc2=size=${SIZE}:rate=30" ;;
  smpte)    SOURCE="smptehdbars=size=${SIZE}:rate=30" ;;
  pal)      SOURCE="pal100bars=size=${SIZE}:rate=30" ;;
  black)    SOURCE="color=c=black:size=${SIZE}:rate=30" ;;
  standby)  SOURCE="color=c=0x14213d:size=${SIZE}:rate=30,drawtext=fontfile=${FONT}:text='PLEASE STAND BY':fontsize=110:fontcolor=white:x=(w-tw)/2:y=(h-th)/2" ;;
  *)
    echo "error: unknown PATTERN '$PATTERN' (use one of: testcard, smpte, pal, black, standby)" >&2
    exit 1
    ;;
esac

if [[ "${1:-}" == "--frame" ]]; then
  ffmpeg -v warning -y -f lavfi -i "$SOURCE" -vf "$CLOCK" -frames:v 1 "${2:?usage: --frame FILE}"
  echo "wrote ${2} (UTC now: $(date -u +%T))"
  exit 0
fi

: "${SRT_HOST:?set SRT_HOST}"
: "${SRT_PASSPHRASE:?set SRT_PASSPHRASE}"
SRT_PORT="${SRT_PORT:-5000}"

QUERY="mode=caller&passphrase=${SRT_PASSPHRASE}&pbkeylen=32&pkt_size=1316"
if [[ -n "${SRT_LATENCY_MS:-}" ]]; then
  QUERY="${QUERY}&latency=$((SRT_LATENCY_MS * 1000))"
fi

exec ffmpeg -re \
  -f lavfi -i "$SOURCE" \
  -f lavfi -i "sine=frequency=1000" \
  -vf "$CLOCK" \
  -c:v libx264 -preset veryfast -tune zerolatency \
  -b:v "$BITRATE" -maxrate "$BITRATE" -bufsize 12M -g 60 -keyint_min 60 \
  -c:a aac \
  -f mpegts "srt://${SRT_HOST}:${SRT_PORT}?${QUERY}"
