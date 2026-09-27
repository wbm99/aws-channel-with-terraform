"""Run source/send-srt.sh in its local --frame mode for every pattern. Needs FFmpeg; skipped without it."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from livectl.source import PATTERNS

SCRIPT = Path(__file__).resolve().parents[2] / "source" / "send-srt.sh"
pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def frame(tmp_path, pattern):
    out = tmp_path / f"{pattern}.png"
    result = subprocess.run([str(SCRIPT), "--frame", str(out)], env={**os.environ, "PATTERN": pattern},
                            capture_output=True, text=True, timeout=60)
    return result, out


@pytest.mark.parametrize("pattern", [key for key, _ in PATTERNS])
def test_every_pattern_the_console_offers_renders_a_frame(tmp_path, pattern):
    result, out = frame(tmp_path, pattern)

    assert result.returncode == 0, result.stderr
    assert out.stat().st_size > 1000


def test_the_patterns_really_differ(tmp_path):
    sizes = {pattern: frame(tmp_path, pattern)[1].read_bytes() for pattern, _ in PATTERNS}

    assert len(set(sizes.values())) == len(PATTERNS)


def test_an_unknown_pattern_is_refused_with_the_list(tmp_path):
    result, _ = frame(tmp_path, "rainbow")

    assert result.returncode == 1
    assert "unknown PATTERN" in result.stderr and "smpte" in result.stderr
