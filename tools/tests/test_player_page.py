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
Hls.Events = { ERROR: 'hlsError', LEVEL_SWITCHED: 'hlsLevelSwitched' };
Hls.ErrorTypes = { NETWORK_ERROR: 'networkError', MEDIA_ERROR: 'mediaError' };
"""


class _Site(SimpleHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path.startswith("/config.json"):
            body = json.dumps({"manifestPath": "/out/v1/demo/index.m3u8"}).encode()
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
