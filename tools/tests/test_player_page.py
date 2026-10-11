"""Drive the deployed player page (modules/player/site) in Chrome, offline. Skipped without Playwright.

hls.js normally comes from a CDN; here that request is answered with a small fake that records what the page asks
of it, so the tests need no network and can make hls.js fail on purpose.
"""

import functools
import json
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")
expect = playwright_api.expect

SITE = Path(__file__).resolve().parents[2] / "modules" / "player" / "site"
HLS_URL = "https://cdn.jsdelivr.net/npm/hls.js@1.5.15/dist/hls.min.js"
FAKE_HLS = """
window.hlsCalls = [];
// Assigned to window on purpose: the page checks window.Hls, which a bare class declaration does not create.
window.Hls = class Hls {
  static isSupported() { return true; }
  constructor() { this.handlers = {}; this.levels = []; window.hls = this; }
  loadSource(url) { window.hlsCalls.push(['loadSource', url]); }
  attachMedia() {}
  recoverMediaError() { window.hlsCalls.push(['recoverMediaError']); }
  on(name, handler) { this.handlers[name] = handler; }
  emit(name, data) { this.handlers[name] && this.handlers[name](name, data); }
};
Hls.Events = { ERROR: 'hlsError', LEVEL_SWITCHED: 'hlsLevelSwitched', LEVEL_UPDATED: 'hlsLevelUpdated' };
Hls.ErrorTypes = { NETWORK_ERROR: 'networkError', MEDIA_ERROR: 'mediaError' };
"""


CLIP = Path(__file__).resolve().parent / "fixtures" / "ad.mp4"
CONFIG = {"manifestPath": "/out/v1/demo/index.m3u8"}  # what config.json says; ad tests set adClip


class _Site(SimpleHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path.startswith("/ads/clip.mp4"):
            body = CLIP.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/config.json"):
            body = json.dumps(CONFIG).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def log_message(self, *args):
        return


@pytest.fixture(scope="module")
def site_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Site, directory=str(SITE)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/"
    server.shutdown()
    server.server_close()


@pytest.fixture
def page():
    with playwright_api.sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        page = browser.new_page()
        page.route(HLS_URL, lambda route: route.fulfill(status=200, content_type="text/javascript", body=FAKE_HLS))
        yield page
        browser.close()


def test_the_full_page_shows_its_title_and_clocks(page, site_url):
    page.goto(site_url)

    expect(page.locator("h1")).to_be_visible()
    expect(page.locator(".stats")).to_be_visible()


def test_embed_mode_shows_only_the_video_filling_the_frame(page, site_url):
    page.set_viewport_size({"width": 800, "height": 450})
    page.goto(site_url + "?embed=1")

    expect(page.locator("h1")).to_be_hidden()
    expect(page.locator(".stats")).to_be_hidden()
    expect(page.locator(".note")).to_be_hidden()
    box = page.locator("video").bounding_box()
    assert (box["width"], box["height"]) == (800, 450)


def test_a_fatal_network_error_before_the_first_segment_is_retried(page, site_url):
    """Live: opened before going live, the page stopped at the first 404 and needed a manual refresh."""
    page.goto(site_url + "?embed=1")
    page.wait_for_function("window.hlsCalls.length === 1")

    page.evaluate("window.hls.emit(Hls.Events.ERROR, { fatal: true, type: Hls.ErrorTypes.NETWORK_ERROR })")

    page.wait_for_function("window.hlsCalls.length === 2", timeout=6000)
    assert page.evaluate("window.hlsCalls") == [["loadSource", "/out/v1/demo/index.m3u8"]] * 2


def test_a_media_error_is_recovered_without_reloading(page, site_url):
    page.goto(site_url)
    page.wait_for_function("window.hlsCalls.length === 1")

    page.evaluate("window.hls.emit(Hls.Events.ERROR, { fatal: true, type: Hls.ErrorTypes.MEDIA_ERROR })")

    assert page.evaluate("window.hlsCalls")[-1] == ["recoverMediaError"]


def test_non_fatal_errors_are_left_to_hls_js(page, site_url):
    page.goto(site_url)
    page.wait_for_function("window.hlsCalls.length === 1")

    page.evaluate("window.hls.emit(Hls.Events.ERROR, { fatal: false, type: Hls.ErrorTypes.NETWORK_ERROR })")
    page.wait_for_timeout(3500)

    assert len(page.evaluate("window.hlsCalls")) == 1


# --- the LIVE button (like YouTube's) -----------------------------------------------------------------


def behind_edge(page, edge, current, paused):
    """Put the fake stream's live edge and the video's position and play state where a test needs them."""
    page.evaluate(f"""() => {{
        window.hls.liveSyncPosition = {edge};
        const video = document.getElementById('video');
        video.currentTime = {current};
        Object.defineProperty(video, 'paused', {{ configurable: true, get: () => {str(paused).lower()} }});
    }}""")


def test_at_the_live_edge_the_badge_says_live(page, site_url):
    page.goto(site_url + "?embed=1")
    page.wait_for_function("window.hlsCalls.length === 1")

    behind_edge(page, edge=100, current=99, paused=False)

    button = page.locator("#live-edge")
    expect(button).to_have_attribute("data-state", "live")
    expect(button).to_have_text("LIVE")


def test_behind_the_edge_it_offers_to_go_live_and_jumps_there(page, site_url):
    page.goto(site_url + "?embed=1")
    page.wait_for_function("window.hlsCalls.length === 1")
    behind_edge(page, edge=100, current=40, paused=False)

    button = page.locator("#live-edge")
    expect(button).to_have_attribute("data-state", "behind")
    expect(button).to_have_text("Go live")

    button.click()

    assert page.evaluate("document.getElementById('video').currentTime") == 100


def test_a_paused_player_is_not_live(page, site_url):
    page.goto(site_url)
    page.wait_for_function("window.hlsCalls.length === 1")

    behind_edge(page, edge=100, current=100, paused=True)

    expect(page.locator("#live-edge")).to_have_attribute("data-state", "behind")


# --- back to live after the browser paused it (Chrome pauses muted video that is not visible) ---------------


def hide(page, hidden):
    page.evaluate(f"document.querySelector('.screen').style.display = '{'none' if hidden else ''}'")
    page.wait_for_timeout(500)


def test_shown_again_after_the_browser_paused_it_the_player_jumps_to_live(page, site_url):
    page.goto(site_url + "?embed=1")
    page.wait_for_function("window.hlsCalls.length === 1")
    behind_edge(page, edge=100, current=40, paused=False)

    hide(page, True)
    page.evaluate("document.getElementById('video').dispatchEvent(new Event('pause'))")  # what Chrome does
    hide(page, False)

    assert page.evaluate("document.getElementById('video').currentTime") == 100


def test_a_pause_the_viewer_made_is_respected(page, site_url):
    page.goto(site_url + "?embed=1")
    page.wait_for_function("window.hlsCalls.length === 1")
    behind_edge(page, edge=100, current=40, paused=True)

    page.evaluate("document.getElementById('video').dispatchEvent(new Event('pause'))")  # while visible: the viewer
    page.wait_for_timeout(500)
    hide(page, True)
    hide(page, False)

    assert page.evaluate("document.getElementById('video').currentTime") == 40


def test_coming_back_to_a_background_tab_jumps_to_live(page, site_url):
    page.goto(site_url + "?embed=1")
    page.wait_for_function("window.hlsCalls.length === 1")
    behind_edge(page, edge=100, current=40, paused=False)

    page.evaluate("""() => {
        const set = (state) => {
            Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => state });
            document.dispatchEvent(new Event('visibilitychange'));
        };
        set('hidden');
        document.getElementById('video').dispatchEvent(new Event('pause'));
        return new Promise((resolve) => setTimeout(() => { set('visible'); resolve(); }, 500));
    }""")
    page.wait_for_timeout(300)

    assert page.evaluate("document.getElementById('video').currentTime") == 100


def test_inside_the_consoles_frame_leaving_and_returning_jumps_to_live(page, site_url):
    """The console hides the frame (display: none) when you leave the Live page. Cross-origin, like the real one."""
    parent = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Parent, player=site_url + "?embed=1"))
    threading.Thread(target=parent.serve_forever, daemon=True).start()
    try:
        page.goto(f"http://localhost:{parent.server_address[1]}/")
        frame = page.locator("#player").element_handle().content_frame()
        frame.wait_for_function("window.hlsCalls && window.hlsCalls.length === 1")
        frame.evaluate("""() => {
            window.hls.liveSyncPosition = 100;
            const video = document.getElementById('video');
            video.currentTime = 40;
            Object.defineProperty(video, 'paused', { configurable: true, get: () => false });
        }""")

        page.evaluate("document.getElementById('player').style.display = 'none'")
        page.wait_for_timeout(500)
        frame.evaluate("document.getElementById('video').dispatchEvent(new Event('pause'))")
        page.evaluate("document.getElementById('player').style.display = ''")
        page.wait_for_timeout(700)

        assert frame.evaluate("document.getElementById('video').currentTime") == 100
    finally:
        parent.shutdown()
        parent.server_close()


class _Parent(SimpleHTTPRequestHandler):
    def __init__(self, *args, player, **kwargs):
        self.player = player
        super().__init__(*args, **kwargs)

    def do_GET(self):  # noqa: N802
        body = f'<!doctype html><iframe id="player" src="{self.player}" width="640" height="360"></iframe>'.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        return


# --- ad breaks ------------------------------------------------------------------------------------------


@pytest.fixture
def ad_clip():
    CONFIG["adClip"] = "/ads/clip.mp4"
    yield
    CONFIG.pop("adClip")


def open_player(page, site_url):
    page.goto(site_url + "?embed=1")
    page.wait_for_function("window.hlsCalls.length === 1")


def announce(page, seconds=2.0, offset_ms=0, ident="4", attr=None, duration="seconds"):
    """hls.js reports a playlist holding one break that starts `offset_ms` from now, and plays at its start."""
    attr = attr if attr is not None else {"SCTE35-OUT": "0xFC30"}
    page.evaluate("""([seconds, offset, ident, attr, kind]) => {
        const start = Date.now() + offset;
        window.breakStart = start;
        const range = { id: ident, startDate: new Date(start), attr,
                        duration: kind === 'seconds' ? seconds : null,
                        plannedDuration: kind === 'planned' ? seconds : null };
        window.hls.emit(Hls.Events.LEVEL_UPDATED, { details: { dateRanges: { [ident]: range } } });
    }""", [seconds, offset_ms, ident, attr, duration])


def play_at(page, ms_after_start):
    page.evaluate("ms => { window.hls.playingDate = new Date(window.breakStart + ms); }", ms_after_start)
    page.wait_for_timeout(400)  # the player looks every 250 ms


def live_muted(page):
    return page.evaluate("document.getElementById('video').muted")


def test_breaks_keeps_scte35_out_ranges_with_a_duration(page, site_url):
    page.goto(site_url)
    found = page.evaluate("""() => AdBreaks.breaks({
        a: { id: 'a', startDate: new Date(1000), duration: 30, plannedDuration: null, attr: { 'SCTE35-OUT': '0xFC' } },
        b: { id: 'b', startDate: new Date(1000), duration: 30, plannedDuration: null, attr: { CLASS: 'chapter' } },
        c: { id: 'c', startDate: new Date(1000), duration: null, plannedDuration: null, attr: { 'SCTE35-OUT': '0xFC' } },
        d: { id: 'd', startDate: new Date(5000), duration: null, plannedDuration: 10, attr: { 'SCTE35-OUT': '0xFC' } },
    })""")
    assert found == [{"id": "a", "startMs": 1000, "endMs": 31000}, {"id": "d", "startMs": 5000, "endMs": 15000}]


def test_due_finds_the_break_under_the_playhead(page, site_url):
    page.goto(site_url)
    answers = page.evaluate("""() => {
        const list = [{ id: 'a', startMs: 1000, endMs: 31000 }];
        const at = (ms, played = []) => AdBreaks.due(list, ms, new Set(played));
        return [at(999), at(1000), at(30999), at(31000), at(5000, ['a'])];
    }""")
    assert answers == [None, {"id": "a", "startMs": 1000, "endMs": 31000},
                       {"id": "a", "startMs": 1000, "endMs": 31000}, None, None]


def test_a_break_overlays_the_clip_and_mutes_live(page, site_url, ad_clip):
    open_player(page, site_url)
    page.evaluate("""() => {
        const video = document.getElementById('video');
        window.livePauses = 0;
        video.pause = () => { window.livePauses += 1; };
    }""")
    announce(page)
    play_at(page, 100)

    expect(page.locator("#ad")).to_be_visible()
    assert live_muted(page) is True
    assert page.evaluate("window.livePauses") == 0  # live keeps playing under the ad, so it is at the edge after


def test_the_break_ends_on_time_and_live_is_unmuted(page, site_url, ad_clip):
    open_player(page, site_url)
    page.evaluate("document.getElementById('video').muted = false")  # the viewer turned the sound on
    announce(page)
    play_at(page, 100)
    assert live_muted(page) is True

    play_at(page, 2000)

    expect(page.locator("#ad")).to_be_hidden()
    assert page.evaluate("document.getElementById('ad').paused") is True
    assert live_muted(page) is False


def test_a_short_clip_loops_through_a_long_break(page, site_url, ad_clip):
    open_player(page, site_url)
    announce(page, seconds=6)
    play_at(page, 100)
    expect(page.locator("#ad")).to_be_visible()
    assert page.evaluate("document.getElementById('ad').loop") is True
    play_at(page, 4000)  # past the 2 s clip, still inside the break
    expect(page.locator("#ad")).to_be_visible()


def test_a_break_plays_once(page, site_url, ad_clip):
    open_player(page, site_url)
    announce(page)
    play_at(page, 100)
    play_at(page, 2100)
    expect(page.locator("#ad")).to_be_hidden()

    page.evaluate("""() => {
        const range = { id: '4', startDate: new Date(window.breakStart), duration: 2, plannedDuration: null,
                        attr: { 'SCTE35-OUT': '0xFC30' } };
        window.hls.emit(Hls.Events.LEVEL_UPDATED, { details: { dateRanges: { '4': range } } });
    }""")
    play_at(page, 500)  # a seek back into the same break

    expect(page.locator("#ad")).to_be_hidden()
    assert live_muted(page) is True  # back to the page's own muted autoplay, not stuck by the break


def test_joining_mid_break_shows_the_rest(page, site_url, ad_clip):
    open_player(page, site_url)
    announce(page)
    play_at(page, 1000)
    expect(page.locator("#ad")).to_be_visible()
    play_at(page, 2000)
    expect(page.locator("#ad")).to_be_hidden()


def test_a_planned_duration_is_used_when_there_is_no_duration(page, site_url, ad_clip):
    open_player(page, site_url)
    announce(page, duration="planned")
    play_at(page, 100)
    expect(page.locator("#ad")).to_be_visible()


def test_without_a_clip_live_stays_on_screen(page, site_url):
    open_player(page, site_url)
    page.evaluate("document.getElementById('video').muted = false")
    announce(page)
    play_at(page, 100)

    assert page.locator("#ad").count() == 0
    assert live_muted(page) is False


def test_a_clip_that_fails_to_load_skips_the_break(page, site_url):
    CONFIG["adClip"] = "/ads/missing.mp4"
    try:
        open_player(page, site_url)
        page.evaluate("document.getElementById('video').muted = false")
        page.wait_for_timeout(500)  # the 404 arrives
        announce(page)
        play_at(page, 100)
        expect(page.locator("#ad")).to_be_hidden()
        assert live_muted(page) is False
    finally:
        CONFIG.pop("adClip")
