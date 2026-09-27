"""Drive the console in Chrome through every scenario. Skipped when Playwright is not installed.

These tests exist because API tests once passed while the page offered a Start button next to NOT DEPLOYED.
They check what a person sees and can click, in every state.
"""

import re
import threading
import time

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")
expect = playwright_api.expect

from livectl.server import make_server  # noqa: E402
from scenarios import OUTPUTS, SCENARIOS, build  # noqa: E402

EXPECTED = {
    "not-deployed": ("Not deployed", ["deploy", "teardown", "scan"]),
    "deploying": ("Deploying…", []),
    "off-air": ("Off air", ["deploy", "teardown", "scan", "go-live"]),
    "going-live": ("Going live…", []),
    "on-air-no-source": ("On air · no source", ["scan", "go-off-air", "source-start"]),
    "on-air-playing": ("On air · playing", ["scan", "go-off-air", "source-start"]),
    "degraded": ("On air · source degraded", ["scan", "go-off-air", "source-start"]),
    "partly-on": ("Partly on", ["scan", "go-live", "go-off-air", "source-start"]),
    "probe-error": ("Unknown", ["deploy", "teardown", "scan", "go-live", "go-off-air"]),
    "source-running": ("On air · playing", ["scan", "go-off-air", "source-stop"]),
    "source-dropped": ("On air · no source", ["scan", "go-off-air", "source-start"]),
}


@pytest.fixture(scope="module")
def browser():
    with playwright_api.sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        yield browser
        browser.close()


@pytest.fixture
def open_scenario(browser):
    opened = []

    def open_(name, view="control"):
        scenario = build(name)
        server = make_server(scenario.console, port=0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        context = browser.new_context(permissions=["clipboard-read", "clipboard-write"])
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(f"http://127.0.0.1:{server.server_address[1]}/#{view}")
        opened.append((scenario, server, context, errors))
        return page, scenario

    yield open_
    for scenario, server, context, errors in opened:
        context.close()
        server.shutdown()
        server.server_close()
        scenario.close()
        assert errors == [], f"JavaScript errors: {errors}"


def test_every_scenario_is_covered():
    assert set(EXPECTED) == set(SCENARIOS)


@pytest.mark.parametrize("name", list(EXPECTED))
def test_each_state_shows_its_verdict_and_only_the_actions_that_can_work(open_scenario, name):
    page, _ = open_scenario(name)
    verdict, actions = EXPECTED[name]

    expect(page.locator("#verdict-text")).to_have_text(verdict, timeout=15000)
    shown = page.locator("#view-control button[data-action]")
    expect(shown).to_have_count(len(actions))
    assert sorted(shown.evaluate_all("els => els.map(e => e.dataset.action)")) == sorted(actions)


def test_nothing_on_any_page_mentions_a_cli_flag(open_scenario):
    for name in ("not-deployed", "probe-error"):
        page, _ = open_scenario(name)
        expect(page.locator("#verdict-text")).not_to_have_text("connecting…")
        assert "--flow-arn" not in page.locator("body").inner_text()


def test_not_deployed_explains_what_to_do(open_scenario):
    page, _ = open_scenario("not-deployed", "live")

    expect(page.locator("#deploy-note")).to_contain_text("Deploy stack")
    expect(page.locator("#player-empty")).to_be_visible()


def test_copy_puts_exactly_the_url_on_the_clipboard(open_scenario):
    page, _ = open_scenario("off-air")

    page.locator('#endpoints button[data-copy="player"]').click()

    assert page.evaluate("navigator.clipboard.readText()") == OUTPUTS["player_url"]


def test_the_ingest_copy_is_the_srt_url(open_scenario):
    page, _ = open_scenario("off-air")

    page.locator('#endpoints button[data-copy="ingest"]').click()

    assert page.evaluate("navigator.clipboard.readText()") == "srt://203.0.113.20:5000"


def test_clicking_a_node_shows_its_details(open_scenario):
    page, _ = open_scenario("on-air-no-source")

    page.locator('#chain button[data-node="srt_source"]').click()

    expect(page.locator("#drawer")).to_be_visible()
    expect(page.locator("#detail h2")).to_contain_text("MediaConnect Source (SRT)")
    expect(page.locator('#chain button[data-node="srt_source"]')).to_have_attribute("aria-pressed", "true")
    expect(page.locator('#chain button[data-node="srt_source"]')).to_have_attribute("data-health", "bad")


def test_a_failed_probe_shows_its_error_on_the_node(open_scenario):
    page, _ = open_scenario("probe-error")

    page.locator('#chain button[data-node="mediaconnect_flow"]').click()

    expect(page.locator("#detail .error")).to_contain_text("AccessDenied")


def test_the_player_frame_loads_the_deployed_player(open_scenario):
    page, _ = open_scenario("off-air")

    expect(page.locator("#player-frame")).to_have_attribute("src", OUTPUTS["player_url"] + "?embed=1")


def test_teardown_needs_the_typed_word_then_runs_terraform(open_scenario):
    page, scenario = open_scenario("off-air")

    page.locator('#maintenance button[data-action="teardown"]').click()
    expect(page.locator("#confirm")).to_be_visible()
    page.locator("#confirm-input").fill("yes")
    page.locator("#confirm-go").click()
    expect(page.locator("#banner")).to_contain_text("destroy")
    assert scenario.commands == []

    page.locator('#maintenance button[data-action="teardown"]').click()
    page.locator("#confirm-input").fill("destroy")
    page.locator("#confirm-go").click()

    expect(page.locator("#job-log")).to_contain_text("terraform -chdir=envs/demo destroy")
    expect(page.locator("#job-title")).to_have_text("Last job: teardown")
    assert scenario.commands[0][2] == "destroy"


def test_a_poll_does_not_wipe_a_half_typed_confirmation(open_scenario):
    page, _ = open_scenario("off-air")
    page.locator('#maintenance button[data-action="teardown"]').click()
    page.locator("#confirm-input").fill("destr")

    page.wait_for_timeout(4500)  # two polls

    expect(page.locator("#confirm-input")).to_have_value("destr")


def test_the_menu_switches_pages_and_a_reload_keeps_the_page(open_scenario):
    page, _ = open_scenario("off-air")
    expect(page.locator("#view-control")).to_be_visible()
    expect(page.locator("#view-live")).to_be_hidden()

    page.locator('.menu [data-page="live"]').click()
    expect(page.locator("#view-live")).to_be_visible()
    expect(page.locator('.menu [data-page="live"]')).to_have_attribute("aria-current", "page")

    page.reload()
    expect(page.locator("#view-live")).to_be_visible()
    expect(page.locator("#chain button[data-node]")).to_have_count(7)


def test_billing_states_show_a_rate(open_scenario):
    page, _ = open_scenario("partly-on")

    expect(page.locator("#cost")).to_contain_text("$0.29 / h")


def test_a_poll_does_not_take_focus_off_the_selected_node(open_scenario):
    page, _ = open_scenario("off-air")
    page.locator('#chain button[data-node="cloudfront_cdn"]').focus()

    page.wait_for_timeout(4500)

    assert page.evaluate("document.activeElement.dataset.node") == "cloudfront_cdn"


def test_the_mediaconnect_tab_reads_events_as_sentences(open_scenario):
    page, _ = open_scenario("off-air", "live")

    page.locator('[data-tab="mediaconnect"]').click()

    expect(page.locator("#log")).to_contain_text("MediaConnect · flow STANDBY → ACTIVE")


def test_the_srt_tab_shows_tr_101_290_flags_and_never_the_passphrase(open_scenario):
    page, _ = open_scenario("source-running", "live")

    page.locator('[data-tab="srt"]').click()

    expect(page.locator("#log")).to_contain_text("TR 101 290: continuity_count_error")
    expect(page.locator("#log")).to_contain_text("passphrase=***")


def test_selecting_a_node_opens_its_log_tab(open_scenario):
    page, _ = open_scenario("off-air", "live")

    page.locator('#chain button[data-node="medialive_channel"]').click()

    expect(page.locator('[data-tab="medialive"]')).to_have_attribute("aria-selected", "true")
    expect(page.locator("#log")).to_contain_text("MediaLive · channel RUNNING")
    expect(page.locator("#log")).to_contain_text("has not written channel logs yet")


def test_a_raw_event_opens_on_click(open_scenario):
    page, _ = open_scenario("off-air", "live")
    page.locator('[data-tab="all"]').click()

    page.locator("#log .has-raw").first.click()

    expect(page.locator("#log .raw").first).to_contain_text('"detail-type"')


def test_mediapackage_says_plainly_that_it_has_no_access_logs(open_scenario):
    page, _ = open_scenario("off-air", "live")

    page.locator('[data-tab="mediapackage"]').click()

    expect(page.locator("#log")).to_contain_text("access logs are not enabled")
    expect(page.locator("#log")).to_contain_text("Ingest")



# --- feedback from the first live run (2026-09-27) ---------------------------------------


def test_job_output_is_on_the_control_page_not_in_the_log_tabs(open_scenario):
    page, _ = open_scenario("off-air")

    expect(page.locator("#job-card")).to_be_visible()
    assert page.locator("#job-card").bounding_box()["width"] > 600, "the job log runs the width of the page"
    expect(page.locator('[data-tab="jobs"]')).to_have_count(0)


def test_the_side_panel_never_grows_past_the_window(open_scenario):
    page, _ = open_scenario("on-air-playing", "live")

    height = page.locator("#panel").bounding_box()["height"]

    assert height <= page.viewport_size["height"]


@pytest.mark.parametrize("name, step", [
    ("not-deployed", "deploy"),
    ("off-air", "go-live"),
    ("on-air-no-source", "source-start"),
])
def test_the_next_step_is_highlighted(open_scenario, name, step):
    page, _ = open_scenario(name)

    highlighted = page.locator("#view-control button[data-next]")

    expect(highlighted).to_have_count(1)
    expect(highlighted).to_have_attribute("data-action", step)


def test_nothing_is_highlighted_once_the_stream_plays(open_scenario):
    page, _ = open_scenario("on-air-playing")

    expect(page.locator("#verdict-text")).to_have_text("On air · playing", timeout=15000)
    expect(page.locator("#view-control button[data-next]")).to_have_count(0)


def test_the_player_reloads_once_when_the_stream_starts_playing(open_scenario):
    page, _ = open_scenario("on-air-playing")

    expect(page.locator("#player-frame")).to_have_attribute("data-reloads", "1", timeout=15000)


def test_the_player_is_not_reloaded_while_nothing_changes(open_scenario):
    page, _ = open_scenario("off-air")
    page.wait_for_timeout(4500)

    assert page.locator("#player-frame").get_attribute("data-reloads") is None


def test_overlapping_log_reads_never_duplicate_lines(open_scenario):
    """Live, a log read takes about a second, so a tab click and the timer can both read from the same cursor.

    Stub reads are instant, so the page's fetch is slowed down here to make the reads overlap.
    """
    page, _ = open_scenario("off-air", "live")
    page.evaluate("""() => {
        const original = window.fetch;
        window.fetch = (url, options) => String(url).includes('api/logs')
            ? new Promise((resolve) => setTimeout(() => resolve(original(url, options)), 800))
            : original(url, options);
    }""")

    page.locator('[data-tab="mediaconnect"]').click()  # first read of this tab, still in flight...
    page.locator('[data-tab="mediaconnect"]').click()  # ...when the second one starts from the same cursor
    page.wait_for_timeout(2500)

    assert page.locator("#log .line", has_text="flow STANDBY → ACTIVE").count() == 1



# --- two pages (2026-09-27) --------------------------------------------------------------------


def test_a_running_job_is_visible_from_the_live_page(open_scenario):
    page, _ = open_scenario("going-live", "live")

    pill = page.locator("#job-pill")
    expect(pill).to_be_visible()
    expect(pill).to_contain_text("go-live")
    expect(pill).to_contain_text("MediaLive channel STARTING")

    pill.click()

    expect(page.locator("#view-control")).to_be_visible()
    expect(page.locator("#job-log")).to_contain_text("MediaLive channel STARTING")


def test_the_step_a_job_belongs_to_says_what_is_happening(open_scenario):
    page, _ = open_scenario("going-live")

    step = page.locator('[data-step="live"]')

    expect(step).to_have_attribute("data-status", "busy")
    expect(step).to_contain_text("Starting the flow, then the channel")


def test_steps_tick_off_what_is_done(open_scenario):
    page, _ = open_scenario("source-running")

    for step in ("deploy", "live", "source"):
        expect(page.locator(f'[data-step="{step}"]')).to_have_attribute("data-status", "done")


def test_tear_down_lives_under_maintenance_not_in_the_steps(open_scenario):
    page, _ = open_scenario("off-air")

    expect(page.locator('#steps [data-action="teardown"]')).to_have_count(0)
    expect(page.locator('#maintenance [data-action="teardown"]')).to_have_count(1)


def test_the_live_page_can_always_stop_billing(open_scenario):
    page, _ = open_scenario("source-running", "live")

    expect(page.locator('#live-actions [data-action="go-off-air"]')).to_be_visible()
    expect(page.locator('#live-actions [data-action="source-stop"]')).to_be_visible()


def test_the_live_page_offers_nothing_to_stop_off_air(open_scenario):
    page, _ = open_scenario("off-air", "live")

    expect(page.locator("#verdict-text")).to_have_text("Off air")
    expect(page.locator("#live-actions button")).to_have_count(0)


def test_the_live_page_shows_the_broadcast_figures(open_scenario):
    page, _ = open_scenario("source-running", "live")

    expect(page.locator('[data-metric="src_bitrate"] dd')).to_have_text("6.0 Mbps")
    expect(page.locator('[data-metric="ml_fps"] dd')).to_have_text("30.0 fps")


def test_the_live_menu_item_pulses_once_the_stream_plays(open_scenario):
    page, _ = open_scenario("source-running")

    live = page.locator('.menu [data-page="live"]')
    expect(live).to_have_class("next", timeout=15000)

    live.click()
    expect(live).not_to_have_class("next")


def test_details_open_in_a_drawer_that_escape_closes(open_scenario):
    page, _ = open_scenario("off-air", "live")

    page.locator('#chain button[data-node="cloudfront_cdn"]').click()
    expect(page.locator("#drawer")).to_be_visible()
    expect(page.locator("#detail h2")).to_contain_text("CloudFront CDN")

    page.keyboard.press("Escape")
    expect(page.locator("#drawer")).to_be_hidden()



def test_a_dropped_source_shows_at_once_not_when_cloudwatch_catches_up(open_scenario):
    """The metric still says connected at 6 Mbps; the MediaConnect event a second ago says it dropped."""
    page, _ = open_scenario("source-dropped", "live")

    expect(page.locator("#verdict-text")).to_have_text("On air · no source")
    expect(page.locator('#chain button[data-node="srt_source"]')).to_have_attribute("data-health", "bad")

    page.locator('#chain button[data-node="srt_source"]').click()
    expect(page.locator("#detail")).to_contain_text("MediaConnect event at")



# --- second live run (2026-09-27) ----------------------------------------------------------------


def test_a_new_job_shows_its_output_from_the_first_line(open_scenario):
    """Live: after go-off-air's nine lines, the teardown's output was read from line nine and looked empty."""
    page, _ = open_scenario("off-air")
    page.locator('#maintenance [data-action="scan"]').click()
    expect(page.locator("#job-title")).to_have_text("Last job: scan")

    page.locator('#maintenance [data-action="teardown"]').click()
    page.locator("#confirm-input").fill("destroy")
    page.locator("#confirm-go").click()

    expect(page.locator("#job-title")).to_have_text("Last job: teardown")
    expect(page.locator("#job-log")).to_have_text(re.compile(
        r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d  \$ terraform -chdir=envs/demo destroy -auto-approve -input=false -no-color\n$"))


def test_live_logs_run_below_the_player_newest_first(open_scenario):
    page, _ = open_scenario("source-running", "live")
    lines = page.locator("#log .line")
    expect(lines.first).to_contain_text("source connected")

    player = page.locator(".player-card").bounding_box()
    panel = page.locator("#panel").bounding_box()
    assert panel["y"] >= player["y"] + player["height"], "the logs sit under the player, not beside it"
    assert panel["width"] > 900, "and run the width of the page"
    expect(lines.last).to_contain_text("flow STANDBY → ACTIVE")


def test_figures_of_resources_that_are_off_show_nothing(open_scenario):
    page, _ = open_scenario("off-air", "live")

    expect(page.locator("#verdict-text")).to_have_text("Off air")
    expect(page.locator('[data-metric="mp_ingress_bytes"] dd')).to_have_text("—")
    expect(page.locator('[data-metric="src_bitrate"] dd')).to_have_text("—")


def test_the_page_recovers_by_itself_when_the_console_comes_back(open_scenario):
    """Live: after switching Wi-Fi off and on, the page needed a reload."""
    page, _ = open_scenario("off-air")
    expect(page.locator("#verdict-text")).to_have_text("Off air")

    page.route("**/api/pipeline*", lambda route: route.abort("internetdisconnected"))
    expect(page.locator("#banner")).to_contain_text("Lost contact", timeout=10000)

    page.unroute("**/api/pipeline*")
    expect(page.locator("#banner")).to_be_hidden(timeout=10000)
    expect(page.locator("#freshness")).to_contain_text("Updated", timeout=5000)


def test_stale_aws_data_is_labelled(open_scenario):
    page, scenario = open_scenario("off-air")
    scenario.console.pipeline.unreachable = lambda: "AWS is not answering (EndpointConnectionError). Showing what it said 40 s ago."

    expect(page.locator("#aws-note")).to_contain_text("Showing what it said 40 s ago", timeout=10000)



def test_every_log_line_starts_with_its_date_and_time(open_scenario):
    page, _ = open_scenario("source-running", "live")

    first = page.locator("#log .line").first
    expect(first.locator("time")).to_have_text(re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$"))
    expect(first).to_contain_text("MediaConnect · source connected")



def test_the_cloudfront_tab_shows_delivery_figures(open_scenario):
    page, _ = open_scenario("source-running", "live")

    page.locator('[data-tab="cloudfront"]').click()

    expect(page.locator("#log")).to_contain_text("Requests / min")
    expect(page.locator("#log")).to_contain_text("42")
    expect(page.locator("#log")).to_contain_text("Viewer counts and edge locations need")



def test_logs_expand_into_their_own_page_and_come_back(open_scenario):
    page, _ = open_scenario("source-running", "live")
    page.locator('[data-tab="mediaconnect"]').click()

    page.locator("#log-expand").click()

    expect(page.locator("#view-logs")).to_be_visible()
    expect(page.locator("#view-logs #panel")).to_be_visible()
    expect(page.locator('[data-tab="mediaconnect"]')).to_have_attribute("aria-selected", "true")
    assert page.locator("#panel").bounding_box()["height"] > page.viewport_size["height"] * 0.55

    page.locator("#log-expand").click()
    expect(page.locator("#view-live #panel")).to_be_visible()


def test_the_filter_keeps_only_matching_lines(open_scenario):
    page, _ = open_scenario("source-running", "logs")
    expect(page.locator("#log .line")).to_have_count(3)

    page.locator("#log-filter").fill("medialive")

    expect(page.locator("#log .line")).to_have_count(1)
    expect(page.locator("#log .line")).to_contain_text("channel RUNNING")

    page.locator("#log-filter").fill("nothing like this")
    expect(page.locator("#log")).to_contain_text('No line matches "nothing like this"')



# --- progress and stopwatch ---------------------------------------------------------------------------


def test_a_terraform_job_shows_how_far_it_has_got(open_scenario):
    page, _ = open_scenario("deploying")

    bar = page.locator("#job-progress")
    expect(bar).to_be_visible()
    expect(bar).to_have_attribute("aria-valuenow", "50")
    expect(bar).to_have_attribute("data-state", "running")
    expect(page.locator("#job-meta")).to_have_text(re.compile(r"^2 of 4 resources · 50% · 0:\d\d$"))
    expect(page.locator("#job-pill")).to_contain_text("deploy · 50% · 0:")


def test_going_live_shows_its_milestones(open_scenario):
    page, _ = open_scenario("going-live")

    expect(page.locator("#job-meta")).to_contain_text("MediaConnect flow active · 50%")


def test_the_stopwatch_runs_between_polls(open_scenario):
    page, _ = open_scenario("deploying")
    meta = page.locator("#job-meta")
    expect(meta).to_contain_text("50%")
    first = meta.inner_text()

    page.wait_for_timeout(2200)

    assert meta.inner_text() != first


def test_a_finished_job_turns_green_and_says_how_long_it_took(open_scenario):
    page, _ = open_scenario("off-air")
    page.locator('#maintenance [data-action="teardown"]').click()
    page.locator("#confirm-input").fill("destroy")
    page.locator("#confirm-go").click()

    expect(page.locator("#job-progress")).to_have_attribute("data-state", "succeeded")
    expect(page.locator("#job-progress")).to_have_attribute("aria-valuenow", "100")
    expect(page.locator("#job-meta")).to_contain_text("took 0:0")
    expect(page.locator("#job-pill")).to_have_attribute("data-state", "succeeded")
    color = page.locator("#job-bar").evaluate("el => getComputedStyle(el).backgroundColor")
    assert color == "rgb(18, 183, 106)", "green (--ok), not the running blue"


def test_the_first_node_is_named_for_the_mediaconnect_source(open_scenario):
    page, _ = open_scenario("off-air")

    expect(page.locator('#chain button[data-node="srt_source"]')).to_contain_text("MediaConnect Source (SRT)")



# --- test pattern ------------------------------------------------------------------------------------------


def test_the_chosen_pattern_is_sent_with_send_test_source(open_scenario):
    page, scenario = open_scenario("on-air-no-source")

    page.locator("#pattern").select_option("smpte")
    page.locator('#steps [data-action="source-start"]').click()

    expect(page.locator('#steps [data-action="source-stop"]')).to_be_visible()
    assert scenario.console.source.calls == [("start", "smpte")]
    expect(page.locator("#pattern")).to_have_value("smpte")


def test_changing_the_pattern_while_sending_switches_it_live(open_scenario):
    page, scenario = open_scenario("source-running")
    expect(page.locator("#pattern")).to_have_value("testcard")
    expect(page.locator("#steps")).to_contain_text("restarts FFmpeg")

    page.locator("#pattern").select_option("standby")

    # The dropdown changes at once; the switch reaches the server a moment later, so wait for the calls themselves.
    deadline = time.monotonic() + 5
    while scenario.console.source.calls != [("stop",), ("start", "standby")] and time.monotonic() < deadline:
        page.wait_for_timeout(100)
    assert scenario.console.source.calls == [("stop",), ("start", "standby")]
    expect(page.locator("#pattern")).to_have_value("standby")
    expect(page.locator("#banner")).to_be_hidden()


def test_choosing_a_pattern_while_not_sending_starts_nothing(open_scenario):
    page, scenario = open_scenario("on-air-no-source")

    page.locator("#pattern").select_option("black")

    expect(page.locator("#pattern")).to_have_value("black")
    assert scenario.console.source.calls == []
