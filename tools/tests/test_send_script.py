"""Run source/send-srt.sh: its argument mapping against a fake FFmpeg, and real encodes where FFmpeg is installed."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from livectl.source import CHOICES, PATTERNS

SCRIPT = Path(__file__).resolve().parents[2] / "source" / "send-srt.sh"
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


# --- local frames (real FFmpeg) ----------------------------------------------------------------------------


def frame(tmp_path, pattern):
    out = tmp_path / f"{pattern}.png"
    result = subprocess.run([str(SCRIPT), "--frame", str(out)], env={**os.environ, "PATTERN": pattern},
                            capture_output=True, text=True, timeout=60)
    return result, out


@needs_ffmpeg
@pytest.mark.parametrize("pattern", [key for key, _ in PATTERNS])
def test_every_pattern_the_console_offers_renders_a_frame(tmp_path, pattern):
    result, out = frame(tmp_path, pattern)

    assert result.returncode == 0, result.stderr
    assert out.stat().st_size > 1000


@needs_ffmpeg
def test_the_patterns_really_differ(tmp_path):
    sizes = {pattern: frame(tmp_path, pattern)[1].read_bytes() for pattern, _ in PATTERNS}

    assert len(set(sizes.values())) == len(PATTERNS)


@needs_ffmpeg
def test_an_unknown_pattern_is_refused_with_the_list(tmp_path):
    result, _ = frame(tmp_path, "rainbow")

    assert result.returncode == 1
    assert "unknown PATTERN" in result.stderr and "smpte" in result.stderr


# --- the FFmpeg command (fake FFmpeg that prints its arguments) --------------------------------------------


@pytest.fixture
def run(tmp_path):
    fake = tmp_path / "bin" / "ffmpeg"
    fake.parent.mkdir()
    fake.write_text("#!/usr/bin/env bash\nprintf '%s\\n' \"$@\"\n")
    fake.chmod(0o755)
    base = {**os.environ, "PATH": f"{fake.parent}:{os.environ['PATH']}", "SRT_HOST": "127.0.0.1",
            "SRT_PASSPHRASE": "x" * 32}
    for name in ("FPS", "SIZE", "BITRATE", "GOP_SECONDS", "AUDIO_CODEC", "AUDIO_BITRATE", "TONE", "SERVICE_NAME",
                 "SERVICE_PROVIDER", "PROGRAM_NUMBER", "SRT_LATENCY_MS", "PATTERN"):
        base.pop(name, None)

    def run_(**env):
        return subprocess.run([str(SCRIPT)], env={**base, **env}, capture_output=True, text=True, timeout=30)

    return run_


def args(run, **env):
    result = run(**env)
    assert result.returncode == 0, result.stderr
    return result.stdout.splitlines()


def after(argv, flag):
    return argv[argv.index(flag) + 1]


def test_the_gop_follows_the_frame_rate(run):
    argv = args(run, FPS="25", GOP_SECONDS="2")

    assert after(argv, "-g") == "50" and after(argv, "-keyint_min") == "50"
    assert "rate=25" in argv[argv.index("-i") + 1]


def test_the_buffer_is_twice_the_bitrate(run):
    argv = args(run, BITRATE="500k")

    assert (after(argv, "-b:v"), after(argv, "-maxrate"), after(argv, "-bufsize")) == ("500k", "500k", "1000k")


def test_the_documented_megabit_form_still_works(run):
    argv = args(run, BITRATE="6M")

    assert (after(argv, "-b:v"), after(argv, "-bufsize")) == ("6000k", "12000k")


def test_the_defaults_match_the_consoles(run):
    argv = args(run)

    assert after(argv, "-b:v") == "6000k" and after(argv, "-g") == "60"
    assert (after(argv, "-c:a"), after(argv, "-b:a")) == ("aac", "128k")
    assert "service_name=livectl test source" in argv and "service_provider=livectl" in argv


def test_the_service_identity_reaches_the_muxer(run):
    argv = args(run, SERVICE_NAME="Jogo ão vivo", PROGRAM_NUMBER="7")

    assert "service_name=Jogo ão vivo" in argv
    assert "service_provider=livectl" in argv
    assert after(argv, "-mpegts_service_id") == "7"


def test_silence_uses_a_silent_stereo_source(run):
    silent, tone = args(run, TONE="silence"), args(run, TONE="440")

    assert "anullsrc=r=48000:cl=stereo" in silent and not any("sine=" in a for a in silent)
    assert "sine=frequency=440:sample_rate=48000" in tone
    for argv in (silent, tone):
        assert after(argv, "-ac") == "2" and after(argv, "-ar") == "48000"


def test_audio_codec_and_bitrate(run):
    argv = args(run, AUDIO_CODEC="mp2", AUDIO_BITRATE="192k")

    assert (after(argv, "-c:a"), after(argv, "-b:a")) == ("mp2", "192k")


def test_latency_is_sent_in_microseconds(run):
    assert "latency=120000" in args(run)[-1]
    assert "latency=500000" in args(run, SRT_LATENCY_MS="500")[-1]


@pytest.mark.parametrize("name,value", [
    ("FPS", "24"), ("SIZE", "3840x2160"), ("BITRATE", "12M"), ("BITRATE", "6.5M"), ("BITRATE", "400k"),
    ("GOP_SECONDS", "3"), ("AUDIO_CODEC", "opus"), ("AUDIO_BITRATE", "100k"), ("TONE", "2000"),
    ("PROGRAM_NUMBER", "0"), ("PROGRAM_NUMBER", "65536"), ("SRT_LATENCY_MS", "10"), ("SERVICE_NAME", "x" * 61),
    ("SERVICE_PROVIDER", ""),
])
def test_bad_values_are_refused(run, name, value):
    result = run(**{name: value})

    assert result.returncode == 1
    assert result.stderr.startswith("error:") and name in result.stderr


def test_sixty_accented_characters_are_sixty_not_a_hundred_and_twenty(run):
    assert "service_name=" + "é" * 60 in args(run, SERVICE_NAME="é" * 60)


def test_the_script_offers_what_the_console_offers(run):
    """Every enum value in CHOICES is accepted by the script: the two lists are kept in step."""
    names = {"size": "SIZE", "fps": "FPS", "gop_seconds": "GOP_SECONDS", "audio_codec": "AUDIO_CODEC",
             "audio_kbps": "AUDIO_BITRATE", "tone": "TONE"}
    for choice in CHOICES:
        if choice["key"] in names:
            for value in choice["values"]:
                suffix = "k" if choice["key"] == "audio_kbps" else ""
                args(run, **{names[choice["key"]]: f"{value['id']}{suffix}"})


# --- real encodes ------------------------------------------------------------------------------------------


@needs_ffmpeg
@pytest.mark.parametrize("codec", ["aac", "mp2", "ac3"])
@pytest.mark.parametrize("bitrate", ["64k", "256k"])
def test_every_audio_codec_really_encodes(run, tmp_path, codec, bitrate):
    argv = args(run, AUDIO_CODEC=codec, AUDIO_BITRATE=bitrate, SIZE="1280x720", BITRATE="1M")
    argv = [a for a in argv if a != "-re"][:-1] + ["-t", "1", str(tmp_path / "out.ts")]

    result = subprocess.run(["ffmpeg", "-v", "error", "-y", *argv], capture_output=True, text=True, timeout=60)

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "out.ts").stat().st_size > 10_000
