#!/usr/bin/env bash
# Push a test stream with a burned-in UTC clock to an SRT listener.
#
# Required environment:
#   SRT_HOST          ingest IP or hostname of the MediaConnect flow (see: livectl status)
#   SRT_PASSPHRASE    SRT passphrase (from Secrets Manager; never put it on the command line)
# Optional environment (the console offers the same values: CHOICES in tools/livectl/source.py; keep the two in step):
#   SRT_PORT          listener port (default 5000)
#   SRT_LATENCY_MS    SRT latency, 20-8000 ms (default 120); FFmpeg expects microseconds, converted here
#   PATTERN           testcard (default), smpte, pal, black, standby
#   SIZE              1280x720 or 1920x1080 (default)
#   FPS               25, 30 (default), 50, 60
#   BITRATE           video bitrate, 500k-10000k, whole k or M (default 6000k); the VBV buffer is twice this
#   GOP_SECONDS       keyframe interval: 1, 2 (default), 4
#   AUDIO_CODEC       aac (default), mp2, ac3
#   AUDIO_BITRATE     64k, 96k, 128k (default), 192k, 256k
#   TONE              440, 1000 (default) or silence
#   SERVICE_NAME      MPEG-TS service name, 1-60 characters (default "livectl test source")
#   SERVICE_PROVIDER  MPEG-TS service provider, 1-60 characters (default "livectl")
#   PROGRAM_NUMBER    MPEG-TS program number, 1-65535 (default 1)
#   FONT              TrueType font for the clock (default DejaVu Sans)
# Every value stays inside the MediaLive channel's input class (AVC, HD, up to 10 Mbps); see modules/encode.
# Local check without any network:
#   source/send-srt.sh --frame /tmp/clock.png

set -euo pipefail

fail() {
  echo "error: $*" >&2
  exit 1
}

# one_of NAME VALUE ALLOWED...
one_of() {
  local name="$1" value="$2"
  shift 2
  local allowed
  for allowed in "$@"; do
    [[ "$value" == "$allowed" ]] && return 0
  done
  fail "unknown $name '$value' (use one of: $(echo "$@" | sed 's/ /, /g'))"
}

# between NAME VALUE MIN MAX: a whole number in range, written without leading zeros (bash would read 0100 as octal)
# and short enough that bash arithmetic cannot overflow
between() {
  [[ "$2" =~ ^[1-9][0-9]{0,5}$ ]] && (( $2 >= $3 && $2 <= $4 )) || fail "$1 must be a whole number from $3 to $4, not '$2'"
}

# text NAME VALUE: 1-60 characters (not bytes), no control characters
text() {
  local length
  length="$(LC_ALL=C.UTF-8 bash -c 'printf %s "${#1}"' _ "$2")"
  if (( length < 1 || length > 60 )) || [[ "$2" =~ [[:cntrl:]] ]]; then
    fail "$1 must be 1 to 60 characters without control characters"
  fi
}

PATTERN="${PATTERN:-testcard}"
SIZE="${SIZE:-1920x1080}"
FPS="${FPS:-30}"
BITRATE="${BITRATE:-6000k}"
GOP_SECONDS="${GOP_SECONDS:-2}"
AUDIO_CODEC="${AUDIO_CODEC:-aac}"
AUDIO_BITRATE="${AUDIO_BITRATE:-128k}"
TONE="${TONE:-1000}"
SERVICE_NAME="${SERVICE_NAME-livectl test source}"
SERVICE_PROVIDER="${SERVICE_PROVIDER-livectl}"
PROGRAM_NUMBER="${PROGRAM_NUMBER:-1}"
SRT_LATENCY_MS="${SRT_LATENCY_MS:-120}"
FONT="${FONT:-/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf}"

one_of PATTERN "$PATTERN" testcard smpte pal black standby
one_of SIZE "$SIZE" 1280x720 1920x1080
one_of FPS "$FPS" 25 30 50 60
one_of GOP_SECONDS "$GOP_SECONDS" 1 2 4
one_of AUDIO_CODEC "$AUDIO_CODEC" aac mp2 ac3
one_of AUDIO_BITRATE "$AUDIO_BITRATE" 64k 96k 128k 192k 256k
one_of TONE "$TONE" 440 1000 silence
text SERVICE_NAME "$SERVICE_NAME"
text SERVICE_PROVIDER "$SERVICE_PROVIDER"
between PROGRAM_NUMBER "$PROGRAM_NUMBER" 1 65535
between SRT_LATENCY_MS "$SRT_LATENCY_MS" 20 8000
case "$BITRATE" in
  *k) VIDEO_KBPS="${BITRATE%k}" ;;
  *M) VIDEO_KBPS="${BITRATE%M}000" ;;
  *) VIDEO_KBPS="" ;;
esac
[[ "$VIDEO_KBPS" =~ ^[0-9]+$ ]] || fail "BITRATE must be a whole number of k or M, like 6000k or 6M, not '$BITRATE'"
between BITRATE "$VIDEO_KBPS" 500 10000

if [[ ! -f "$FONT" ]]; then
  fail "font not found: $FONT (install fonts-dejavu-core or set FONT)"
fi

# %T is HH:MM:SS, so the only colon that needs escaping is the one after gmtime.
CLOCK="drawtext=fontfile=${FONT}:text='%{gmtime\:%T}':fontsize=96:fontcolor=white:box=1:boxcolor=black@0.6:x=40:y=40"
# Every pattern is an FFmpeg built-in generator; the UTC clock is burned in on top of all of them.
case "$PATTERN" in
  testcard) SOURCE="testsrc2=size=${SIZE}:rate=${FPS}" ;;
  smpte)    SOURCE="smptehdbars=size=${SIZE}:rate=${FPS}" ;;
  pal)      SOURCE="pal100bars=size=${SIZE}:rate=${FPS}" ;;
  black)    SOURCE="color=c=black:size=${SIZE}:rate=${FPS}" ;;
  standby)  SOURCE="color=c=0x14213d:size=${SIZE}:rate=${FPS},drawtext=fontfile=${FONT}:text='PLEASE STAND BY':fontsize=110:fontcolor=white:x=(w-tw)/2:y=(h-th)/2" ;;
esac

if [[ "${1:-}" == "--frame" ]]; then
  ffmpeg -v warning -y -f lavfi -i "$SOURCE" -vf "$CLOCK" -frames:v 1 "${2:?usage: --frame FILE}"
  echo "wrote ${2} (UTC now: $(date -u +%T))"
  exit 0
fi

: "${SRT_HOST:?set SRT_HOST}"
: "${SRT_PASSPHRASE:?set SRT_PASSPHRASE}"
SRT_PORT="${SRT_PORT:-5000}"

if [[ "$TONE" == "silence" ]]; then
  AUDIO="anullsrc=r=48000:cl=stereo"
else
  AUDIO="sine=frequency=${TONE}:sample_rate=48000"
fi
GOP=$((FPS * GOP_SECONDS))
QUERY="mode=caller&passphrase=${SRT_PASSPHRASE}&pbkeylen=32&pkt_size=1316&latency=$((SRT_LATENCY_MS * 1000))"

args=(
  -re
  -f lavfi -i "$SOURCE"
  -f lavfi -i "$AUDIO"
  -vf "$CLOCK"
  -c:v libx264 -preset veryfast -tune zerolatency
  -b:v "${VIDEO_KBPS}k" -maxrate "${VIDEO_KBPS}k" -bufsize "$((VIDEO_KBPS * 2))k" -g "$GOP" -keyint_min "$GOP"
  -c:a "$AUDIO_CODEC" -b:a "$AUDIO_BITRATE" -ac 2 -ar 48000
  -metadata "service_name=${SERVICE_NAME}" -metadata "service_provider=${SERVICE_PROVIDER}"
  -mpegts_service_id "$PROGRAM_NUMBER"
  -f mpegts "srt://${SRT_HOST}:${SRT_PORT}?${QUERY}"
)
exec ffmpeg "${args[@]}"
