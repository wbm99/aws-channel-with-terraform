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
    "no-credentials": ("No AWS credentials", []),
    "root-identity": ("Off air", ["deploy", "teardown", "scan", "go-live"]),
    "source-received-mismatch": ("On air · playing", ["scan", "go-off-air", "source-stop"]),
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



# --- the source step -------------------------------------------------------------------------------------


def test_the_source_step_summarises_the_settings_and_links_to_the_source_page(open_scenario):
    page, _ = open_scenario("on-air-no-source")

    expect(page.locator("#source-summary")).to_have_text(
        'Test card · 1080p30 · 6 Mbps · AAC 128k · "livectl test source" — Edit on Source')
    expect(page.locator("#pattern")).to_have_count(0)

    page.locator("#source-summary a").click()
    page.wait_for_function("location.hash === '#source'")


def test_send_test_source_on_control_starts_the_saved_settings(open_scenario):
    from livectl.source import SourceSettings

    page, scenario = open_scenario("on-air-no-source")

    page.locator('#steps [data-action="source-start"]').click()

    expect(page.locator('#steps [data-action="source-stop"]')).to_be_visible()
    assert scenario.console.source.calls == [("start", SourceSettings())]


# --- the Source page --------------------------------------------------------------------------------------


def calls_settle(scenario, expected_len, timeout=5):
    deadline = time.monotonic() + timeout
    while len(scenario.console.source.calls) < expected_len and time.monotonic() < deadline:
        time.sleep(0.05)
    return scenario.console.source.calls


def test_the_form_is_built_from_the_choices(open_scenario):
    page, _ = open_scenario("on-air-no-source", "source")
    form = page.locator("#source-form")

    expect(form.locator("[name]")).to_have_count(12)
    expect(form.locator("legend")).to_have_text(["Video", "Audio", "MPEG-TS", "SRT"])
    assert form.locator('[name="fps"] option').all_inner_texts() == ["25", "30", "50", "60"]
    bitrate = form.locator('[name="video_kbps"]')
    expect(bitrate).to_have_value("6")
    assert [bitrate.get_attribute(a) for a in ("min", "max", "step")] == ["0.5", "10", "0.1"]
    expect(form.locator('[name="service_name"]')).to_have_attribute("maxlength", "60")
    expect(page.locator('.menu a[data-page]')).to_have_text(["Control", "Source", "Live", "Logs"])


def test_two_edits_make_one_apply_with_only_those_fields(open_scenario):
    page, scenario = open_scenario("source-running", "source")
    apply = page.locator("#source-apply")
    expect(apply).to_have_text("Apply")
    expect(apply).to_be_disabled()

    page.locator('[name="fps"]').select_option("50")
    page.locator('[name="service_name"]').fill("Match 1")
    expect(apply).to_have_text("Apply 2 changes")
    expect(page.locator('#source-form [data-changed]')).to_have_count(2)
    with page.expect_request("**/api/source/settings") as request:
        apply.click()

    assert request.value.post_data_json == {"fps": 50, "service_name": "Match 1"}
    calls = calls_settle(scenario, 1)
    assert [(kind, s.fps, s.service_name) for kind, s in calls] == [("restart", 50, "Match 1")]
    expect(page.locator("#source-note")).to_contain_text("Restarting FFmpeg")
    expect(apply).to_have_text("Apply")
    expect(apply).to_be_disabled()
    expect(page.locator('#source-form [data-changed]')).to_have_count(0)


def test_while_stopped_the_main_button_sends(open_scenario):
    page, scenario = open_scenario("on-air-no-source", "source")
    apply = page.locator("#source-apply")
    expect(apply).to_have_text("Send test source")
    expect(page.locator("#source-stop")).to_be_hidden()

    page.locator('[name="pattern"]').select_option("smpte")
    apply.click()

    calls = calls_settle(scenario, 1)
    assert [(kind, s.pattern) for kind, s in calls] == [("start", "smpte")]
    expect(page.locator("#source-stop")).to_be_visible()


def test_an_invalid_bitrate_is_shown_under_its_field(open_scenario):
    page, scenario = open_scenario("source-running", "source")
    bitrate = page.locator('[name="video_kbps"]')
    error = page.locator('[data-error-for="video_kbps"]')

    bitrate.fill("20")
    page.locator("#source-apply").click()
    expect(error).to_contain_text("between 500 and 10000")
    expect(bitrate).to_be_focused()

    bitrate.fill("")
    page.locator("#source-apply").click()
    expect(error).to_contain_text("whole number")
    assert scenario.console.source.calls == []

    bitrate.fill("4.5")
    expect(error).to_be_empty()


def test_discard_and_reset(open_scenario):
    page, scenario = open_scenario("on-air-no-source", "source")
    scenario.console.settings = scenario.console.settings.merged({"fps": 25})
    fps = page.locator('[name="fps"]')
    expect(fps).to_have_value("25")

    fps.select_option("50")
    page.locator("#source-discard").click()
    expect(fps).to_have_value("25")
    expect(page.locator("#source-discard")).to_be_disabled()

    page.locator("#source-reset").click()
    expect(fps).to_have_value("30")
    expect(page.locator('#source-form [data-changed]')).to_have_count(1)
    assert scenario.console.settings.fps == 25, "reset only fills the form; nothing is sent"


def test_a_poll_never_overwrites_an_edited_field(open_scenario):
    page, _ = open_scenario("source-running", "source")
    name = page.locator('[name="service_name"]')
    name.fill("Half typed")
    page.locator('[name="fps"]').focus()

    page.wait_for_timeout(2500)

    expect(name).to_have_value("Half typed")


def test_another_tabs_change_reaches_an_unedited_form(open_scenario):
    page, scenario = open_scenario("source-running", "source")
    expect(page.locator('[name="fps"]')).to_have_value("30")

    scenario.console.settings = scenario.console.settings.merged({"fps": 60})

    expect(page.locator('[name="fps"]')).to_have_value("60", timeout=5000)
    expect(page.locator("#source-apply")).to_be_disabled()


def test_sent_and_received_agree(open_scenario):
    page, _ = open_scenario("source-running", "source")
    rows = page.locator("#source-received tr[data-row]")

    expect(rows).to_have_count(3, timeout=8000)
    expect(page.locator("#source-received tr[data-mismatch]")).to_have_count(0)
    expect(page.locator('#source-received tr[data-row="video"]')).to_contain_text("1920×1080")


def test_a_renamed_service_is_highlighted(open_scenario):
    page, _ = open_scenario("source-received-mismatch", "source")
    program = page.locator('#source-received tr[data-row="program"]')

    expect(program).to_have_attribute("data-mismatch", "", timeout=8000)
    expect(program).to_contain_text("livectl test source")
    expect(program).to_contain_text("Service01")
    expect(page.locator('#source-received tr[data-row="video"]')).not_to_have_attribute("data-mismatch", "")


def test_received_says_why_when_idle(open_scenario):
    page, _ = open_scenario("off-air", "source")

    expect(page.locator("#source-received-note")).to_contain_text("not active", timeout=8000)
    expect(page.locator("#source-received")).to_be_hidden()


def test_send_is_refused_with_the_controls_message(open_scenario):
    page, _ = open_scenario("off-air", "source")

    expect(page.locator("#source-apply")).to_be_disabled()
    expect(page.locator("#source-note")).to_contain_text("go live first")


def test_the_source_page_stacks_on_a_narrow_window(open_scenario):
    page, _ = open_scenario("source-running", "source")
    page.set_viewport_size({"width": 600, "height": 900})

    form = page.locator("#source-form").bounding_box()
    received = page.locator("#received-card").bounding_box()
    assert received["y"] > form["y"] + form["height"]
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_the_header_says_who_the_console_acts_as(open_scenario):
    page, _ = open_scenario("on-air-playing")

    expect(page.locator("#identity")).to_have_text(
        "acting as role LiveOps (william) · account 123456789012 · us-east-1")
    expect(page.locator("#identity-banner")).to_be_hidden()


def test_root_credentials_raise_a_red_banner(open_scenario):
    page, _ = open_scenario("root-identity")

    banner = page.locator("#identity-banner")
    expect(banner).to_be_visible()
    expect(banner).to_have_text(re.compile(r"^You are using the account's root user"))
    expect(banner).to_have_attribute("data-kind", "root")
    expect(page.locator("#identity")).to_contain_text("acting as root")


def test_no_credentials_shows_one_banner_and_no_chain(open_scenario):
    page, _ = open_scenario("no-credentials")

    expect(page.locator("#identity-banner")).to_contain_text("aws configure")
    expect(page.locator("#identity")).to_be_hidden()
    expect(page.locator("#chain button[data-node]")).to_have_count(0)
    expect(page.locator("#deploy-note")).to_have_text(
        "The pipeline appears here once the console can use your AWS credentials (see above).")
    expect(page.locator('#steps [data-status="current"]')).to_have_count(0)
    expect(page.locator("#cost")).to_have_text("cost unknown")  # the stack may still be on air


# --- the header clocks and an encoder of your own --------------------------------------------------------


def test_the_header_shows_utc_and_utc_minus_3(open_scenario):
    page, _ = open_scenario("on-air-playing")
    page.clock.set_fixed_time("2026-10-09T01:02:03Z")

    expect(page.locator("#clocks")).to_have_text("UTC 01:02:03UTC−3 22:02:03")
    header = page.locator("header.top").bounding_box()
    clocks = page.locator("#clocks").bounding_box()
    middle = clocks["x"] + clocks["width"] / 2
    assert header["width"] * 0.3 < middle < header["width"] * 0.7


def test_the_passphrase_is_hidden_until_shown_and_hidden_again(open_scenario):
    page, _ = open_scenario("source-running", "source")
    passphrase, url = page.locator("#encoder-passphrase"), page.locator("#encoder-url")

    expect(page.locator("#encoder-address")).to_have_text("srt://203.0.113.20:5000")
    expect(passphrase).to_have_text("••••••••••••")
    expect(url).to_have_text("srt://203.0.113.20:5000?mode=caller&passphrase=••••••••••••&pbkeylen=32"
                             "&pkt_size=1316&latency=120000")
    expect(page.locator("#encoder-latency")).to_have_text("120")

    page.locator("#encoder-show").click()
    expect(passphrase).to_have_text("0123456789abcdef0123456789abcdef")
    expect(url).to_have_text("srt://203.0.113.20:5000?mode=caller&passphrase=0123456789abcdef0123456789abcdef"
                             "&pbkeylen=32&pkt_size=1316&latency=120000")
    expect(page.locator("#encoder-show")).to_have_text("Hide")

    page.locator("#encoder-show").click()
    expect(passphrase).to_have_text("••••••••••••")


def test_copy_puts_the_full_url_on_the_clipboard_without_showing_it(open_scenario):
    page, _ = open_scenario("source-running", "source")

    page.locator("#encoder-copy-url").click()

    expect(page.locator("#encoder-copy-url")).to_have_text("Copied")
    assert page.evaluate("navigator.clipboard.readText()") == (
        "srt://203.0.113.20:5000?mode=caller&passphrase=0123456789abcdef0123456789abcdef&pbkeylen=32"
        "&pkt_size=1316&latency=120000")
    expect(page.locator("#encoder-passphrase")).to_have_text("••••••••••••")

    page.locator("#encoder-copy-passphrase").click()
    expect(page.locator("#encoder-copy-passphrase")).to_have_text("Copied")
    assert page.evaluate("navigator.clipboard.readText()") == "0123456789abcdef0123456789abcdef"


def test_a_shown_url_follows_a_new_latency(open_scenario):
    page, _ = open_scenario("source-running", "source")
    page.locator("#encoder-show").click()
    expect(page.locator("#encoder-url")).to_contain_text("latency=120000")

    page.locator("#source-latency_ms").fill("400")
    page.locator("#source-apply").click()

    expect(page.locator("#encoder-url")).to_contain_text("passphrase=0123456789abcdef")
    expect(page.locator("#encoder-url")).to_contain_text("latency=400000")
    expect(page.locator("#encoder-latency")).to_have_text("400")


def test_the_encoder_card_waits_for_a_deployed_stack(open_scenario):
    page, _ = open_scenario("not-deployed", "source")

    expect(page.locator("#encoder-address")).to_have_text("Deploy the stack first")
    for button in ("show", "copy-address", "copy-passphrase", "copy-url"):
        expect(page.locator(f"#encoder-{button}")).to_be_disabled()


def test_the_encoder_card_stacks_on_a_narrow_window(open_scenario):
    page, _ = open_scenario("source-running", "source")
    page.set_viewport_size({"width": 600, "height": 900})
    page.locator("#encoder-show").click()

    expect(page.locator("#encoder-show")).to_have_text("Hide")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
