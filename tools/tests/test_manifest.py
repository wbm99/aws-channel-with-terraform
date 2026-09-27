from livectl.manifest import ManifestWatcher, first_variant, media_sequence

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
