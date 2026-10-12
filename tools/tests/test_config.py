import json

import pytest

from livectl.config import CLIP_PATH, CONFIG_FILE, AdsSettings, ConfigError, ConfigStore, pending

CLIP = b"\0\0\0\x18ftypisom" + b"\0" * 64


def written(tmp_path) -> dict:
    return json.loads((tmp_path / CONFIG_FILE).read_text())


def test_no_file_means_default_settings(tmp_path):
    assert ConfigStore(str(tmp_path)).ads() == AdsSettings()


def test_saving_keeps_keys_it_does_not_own(tmp_path):
    (tmp_path / CONFIG_FILE).write_text('{"other": 1}')
    ConfigStore(str(tmp_path)).save_ads(AdsSettings(duration_mode="per_trigger"))
    assert written(tmp_path)["other"] == 1
    assert written(tmp_path)["ads"]["duration_mode"] == "per_trigger"


def test_a_malformed_file_is_refused_not_overwritten(tmp_path):
    (tmp_path / CONFIG_FILE).write_text("{")
    store = ConfigStore(str(tmp_path))
    with pytest.raises(ConfigError):
        store.read()
    with pytest.raises(ConfigError):
        store.save_ads(AdsSettings())
    assert (tmp_path / CONFIG_FILE).read_text() == "{"


def test_a_file_that_is_not_an_object_is_refused(tmp_path):
    (tmp_path / CONFIG_FILE).write_text("[]")
    with pytest.raises(ConfigError):
        ConfigStore(str(tmp_path)).read()


def test_saving_a_clip_writes_it_and_its_length(tmp_path):
    store = ConfigStore(str(tmp_path))
    ads = store.save_clip(CLIP, 24.5)
    assert (tmp_path / CLIP_PATH).read_bytes() == CLIP
    assert ads == store.ads() == AdsSettings(clip=CLIP_PATH, clip_seconds=24.5, duration_mode="per_trigger")
    assert store.clip_size() == len(CLIP)


def test_writes_leave_no_temporary_files(tmp_path):
    store = ConfigStore(str(tmp_path))
    store.save_clip(CLIP, 24.5)
    store.save_ads(store.ads().with_mode("clip"))
    assert sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*")) == ["ads", CLIP_PATH, CONFIG_FILE]


def test_removing_the_clip_falls_back_to_per_trigger(tmp_path):
    store = ConfigStore(str(tmp_path))
    store.save_clip(CLIP, 24.5)
    store.save_ads(store.ads().with_mode("clip"))
    assert store.remove_clip() == AdsSettings()
    assert not (tmp_path / CLIP_PATH).exists()
    assert store.ads() == AdsSettings()


def test_a_clip_deleted_by_hand_reads_as_no_clip(tmp_path):
    store = ConfigStore(str(tmp_path))
    store.save_clip(CLIP, 24.5)
    (tmp_path / CLIP_PATH).unlink()
    assert store.ads().clip is None
    assert store.clip_md5() is None
    assert store.clip_size() is None


@pytest.mark.parametrize("data, field", [
    ({"duration_mode": "sometimes"}, "duration_mode"),
    ({"clip": "x.mov", "clip_seconds": 10}, "clip"),
    ({"clip": "ads/clip.mp4"}, "clip_seconds"),
    ({"clip": "ads/clip.mp4", "clip_seconds": 0}, "clip_seconds"),
    ({"duration_mode": "clip"}, "duration_mode"),
    ({"clip": "ads/clip.mp4", "clip_seconds": 4.9, "duration_mode": "clip"}, "duration_mode"),
    ({"clip": "ads/clip.mp4", "clip_seconds": 180.1, "duration_mode": "clip"}, "duration_mode"),
])
def test_validation_matches_terraform(data, field):
    with pytest.raises(ConfigError) as caught:
        AdsSettings.from_dict(data)
    assert caught.value.field == field


def test_pending():
    saved = AdsSettings(clip=CLIP_PATH, clip_seconds=24.5, duration_mode="clip")
    deployed = {"clip_md5": "abc", "clip_seconds": 24.5, "duration_mode": "clip"}
    assert pending(saved, "abc", None) is False
    assert pending(saved, "abc", deployed) is False
    assert pending(saved.with_mode("per_trigger"), "abc", deployed) is True
    assert pending(saved, "def", deployed) is True


def test_a_clip_outside_the_break_range_still_works_per_trigger():
    assert AdsSettings.from_dict({"clip": "ads/clip.mp4", "clip_seconds": 2.0}).duration_mode == "per_trigger"


def test_a_null_mode_means_per_trigger_as_for_terraform():
    assert AdsSettings.from_dict({"duration_mode": None}) == AdsSettings()
