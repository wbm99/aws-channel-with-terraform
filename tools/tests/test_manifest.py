from livectl.manifest import DateRange, ManifestWatcher, date_ranges, first_variant, media_sequence

MASTER = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=5500000,RESOLUTION=1920x1080
index_1.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=3300000,RESOLUTION=1280x720
index_2.m3u8
"""


def media(seq):
    return f"#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXT-X-MEDIA-SEQUENCE:{seq}\n#EXTINF:6.0,\nseg_{seq}.ts\n"


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


URL = "https://d1.cloudfront.net/out/v1/x/x/x-hls/index.m3u8"


def site(pages):
    def fetch(url):
        page = pages[url]
        if isinstance(page, Exception):
            raise page
        return page() if callable(page) else page

    return fetch


def test_the_first_variant_is_resolved_against_the_master_url():
    assert first_variant(MASTER, URL) == "https://d1.cloudfront.net/out/v1/x/x/x-hls/index_1.m3u8"


def test_media_sequence_is_read_from_a_media_playlist():
    assert media_sequence(media(1234)) == 1234
    assert media_sequence(MASTER) is None


def test_the_first_look_cannot_tell_whether_it_is_advancing():
    watcher = ManifestWatcher(site({URL: MASTER, URL.replace("index", "index_1"): media(10)}), Clock())

    check = watcher.check(URL)

    assert (check.sequence, check.advancing, check.error) == (10, False, None)


def test_a_growing_sequence_is_advancing_until_it_stalls_past_the_window():
    clock, seq = Clock(), {"n": 10}
    variant = URL.replace("index", "index_1")
    watcher = ManifestWatcher(site({URL: MASTER, variant: lambda: media(seq["n"])}), clock, window=30)
    watcher.check(URL)

    clock.now, seq["n"] = 6, 11
    assert watcher.check(URL).advancing is True

    clock.now = 36.5  # no new segment for 30.5 s
    assert watcher.check(URL).advancing is False


def test_a_fetch_error_is_reported_not_raised():
    watcher = ManifestWatcher(site({URL: OSError("HTTP Error 404: Not Found")}), Clock())

    check = watcher.check(URL)

    assert check.advancing is False and "404" in check.error


BREAKS = (
    '#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:7\n#EXT-X-PROGRAM-DATE-TIME:2026-10-11T12:03:04.000Z\n'
    '#EXT-X-DATERANGE:ID="4",START-DATE="2026-10-11T12:03:10.000Z",DURATION=30.0,SCTE35-OUT=0xFC302000\n'
    '#EXT-X-DATERANGE:ID="chapter",CLASS="com.example,chapter",START-DATE="2026-10-11T12:03:00.000Z"\n'
    '#EXTINF:6.0,\nseg_7.ts\n'
)


def test_dateranges_with_scte35_out_are_breaks():
    assert date_ranges(BREAKS) == (DateRange("4", "2026-10-11T12:03:10.000Z", 30.0, None),)


def test_planned_duration_is_the_fallback():
    playlist = '#EXT-X-DATERANGE:ID="5",START-DATE="2026-10-11T12:04:00Z",PLANNED-DURATION=24,SCTE35-OUT=0xFC\n'
    assert date_ranges(playlist)[0].seconds == 24.0


def test_no_duration_gives_none():
    playlist = '#EXT-X-DATERANGE:ID="6",START-DATE="2026-10-11T12:05:00Z",SCTE35-OUT=0xFC\n'
    assert date_ranges(playlist)[0].seconds is None


def test_the_check_carries_the_variant_breaks():
    watcher = ManifestWatcher(site({URL: MASTER, URL.replace("index.m3u8", "index_1.m3u8"): BREAKS}), Clock())
    assert [b.id for b in watcher.check(URL).breaks] == ["4"]


# A splice_info_section with a splice_insert (command type 5) whose splice_event_id is 1760184190 (0x68EA477E).
SPLICE_INSERT = "0xFC302500000000000000FFF01405" + "68EA477E" + "7FEF7FFE00294F0E0000000000000A0008435545490000000062B0A1BB"


def test_the_splice_event_id_is_read_from_scte35_out():
    playlist = f'#EXT-X-DATERANGE:ID="x",START-DATE="2026-10-11T12:03:10Z",DURATION=30,SCTE35-OUT={SPLICE_INSERT}\n'
    assert date_ranges(playlist)[0].event_id == 1_760_184_190


def test_an_unreadable_scte35_out_has_no_event_id():
    playlist = '#EXT-X-DATERANGE:ID="x",START-DATE="2026-10-11T12:03:10Z",DURATION=30,SCTE35-OUT=0xFC30\n'
    assert date_ranges(playlist)[0].event_id is None
