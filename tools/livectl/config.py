"""The console's settings for the stack, kept where Terraform reads them.

The Configuration page writes `console.auto.tfvars.json` in the Terraform directory. Terraform loads any
`*.auto.tfvars.json` on its own, so a deploy from the console and a `just up` in a terminal see the same settings.
The console owns only the keys defined here (`ads` for now) and leaves anything else in the file as it found it. A
file it cannot parse is reported and never overwritten: it may hold a person's hand edits.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

CONFIG_FILE = "console.auto.tfvars.json"
CLIP_PATH = "ads/clip.mp4"  # relative to the Terraform directory, as written in the file
MAX_CLIP_BYTES = 50 * 1024 * 1024
DURATION_MODES = ("clip", "per_trigger")
MIN_BREAK_S, MAX_BREAK_S = 5, 180


class ConfigError(RuntimeError):
    """The settings file cannot be read, or a value is outside what Terraform accepts. `field` names the setting."""

    def __init__(self, message: str, field: Optional[str] = None) -> None:
        super().__init__(message)
        self.message, self.field = message, field


@dataclass(frozen=True)
class AdsSettings:
    """The `ads` variable of envs/demo. The rules mirror modules/player's validation, so the page refuses what a
    deploy would."""

    clip: Optional[str] = None
    clip_seconds: Optional[float] = None
    duration_mode: str = "per_trigger"

    def __post_init__(self) -> None:
        if self.duration_mode not in DURATION_MODES:
            raise ConfigError('The duration mode must be "clip" or "per_trigger".', "duration_mode")
        if self.clip is not None and not self.clip.endswith(".mp4"):
            raise ConfigError("The clip must be an .mp4 file.", "clip")
        if self.clip is not None and not (isinstance(self.clip_seconds, (int, float)) and self.clip_seconds > 0):
            raise ConfigError("The clip's length must be a number of seconds greater than 0.", "clip_seconds")
        if self.duration_mode == "clip" and self.clip is None:
            raise ConfigError("Using the clip's length needs a clip.", "duration_mode")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AdsSettings":
        return cls(clip=data.get("clip"), clip_seconds=data.get("clip_seconds"),
                   duration_mode=data.get("duration_mode", "per_trigger"))

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    def with_mode(self, mode: str) -> "AdsSettings":
        return dataclasses.replace(self, duration_mode=mode)


def _write_atomically(target: Path, data: bytes) -> None:
    """Write next to the target, then rename: a failed write never leaves half a file behind."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, prefix=f".{target.name}.", delete=False) as handle:
        handle.write(data)
    os.replace(handle.name, target)


class ConfigStore:
    """Reads and writes the console's settings file and the ad clip in one Terraform directory."""

    def __init__(self, tf_dir: str) -> None:
        self._dir = Path(tf_dir)
        self._file = self._dir / CONFIG_FILE
        self._clip = self._dir / CLIP_PATH
        self._md5: Optional[tuple[tuple[int, int], str]] = None  # (mtime, size) -> digest: the page polls every second

    def read(self) -> dict:
        """The whole file as a dict; {} when there is none."""
        try:
            text = self._file.read_text()
        except FileNotFoundError:
            return {}
        try:
            data = json.loads(text)
        except json.JSONDecodeError as error:
            raise ConfigError(f"{self._file} is not valid JSON (line {error.lineno}). Fix or delete it; the console "
                              "will not overwrite it.") from error
        if not isinstance(data, dict):
            raise ConfigError(f"{self._file} must hold a JSON object. Fix or delete it; the console will not "
                              "overwrite it.")
        return data

    def ads(self) -> AdsSettings:
        """The saved ad settings. A clip whose file is gone (deleted by hand) reads as no clip."""
        try:
            ads = AdsSettings.from_dict(self.read().get("ads") or {})
        except ConfigError as error:
            if error.field is None:
                raise
            raise ConfigError(f"{self._file}: {error.message}", error.field) from error
        if ads.clip is not None and not (self._dir / ads.clip).is_file():
            return AdsSettings()
        return ads

    def clip_md5(self) -> Optional[str]:
        """The clip's MD5, as Terraform's filemd5 gives it, or None without a clip."""
        try:
            stat = self._clip.stat()
            key = (stat.st_mtime_ns, stat.st_size)
            if self._md5 is None or self._md5[0] != key:
                self._md5 = (key, hashlib.md5(self._clip.read_bytes()).hexdigest())
        except FileNotFoundError:
            return None
        return self._md5[1]

    def clip_size(self) -> Optional[int]:
        try:
            return self._clip.stat().st_size
        except FileNotFoundError:
            return None

    def save_ads(self, ads: AdsSettings) -> None:
        data = self.read()  # raises on a malformed file, so it is never overwritten
        data["ads"] = {key: value for key, value in ads.to_dict().items() if value is not None}
        _write_atomically(self._file, (json.dumps(data, indent=2) + "\n").encode())

    def save_clip(self, data: bytes, seconds: float) -> AdsSettings:
        """Store the clip and its length together; the duration mode is kept."""
        current = self.ads()
        ads = AdsSettings(clip=CLIP_PATH, clip_seconds=seconds, duration_mode=current.duration_mode)
        self.read()  # refuse before touching the clip if the settings file cannot be saved
        _write_atomically(self._clip, data)
        self.save_ads(ads)
        return ads

    def remove_clip(self) -> AdsSettings:
        """Delete the clip. Without a clip there is no clip length to use, so breaks fall back to per trigger."""
        ads = AdsSettings()
        self.save_ads(ads)
        self._clip.unlink(missing_ok=True)
        return ads


def pending(saved: AdsSettings, saved_md5: Optional[str], deployed: Optional[Mapping[str, Any]]) -> bool:
    """Whether the saved settings differ from what is deployed. Nothing deployed means nothing pending: the next
    deploy applies the settings anyway."""
    if deployed is None:
        return False
    return (saved_md5 != deployed.get("clip_md5")
            or saved.clip_seconds != deployed.get("clip_seconds")
            or saved.duration_mode != deployed.get("duration_mode"))
