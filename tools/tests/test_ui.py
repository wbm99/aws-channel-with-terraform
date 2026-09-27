"""Drive the console in Chrome through every scenario. Skipped when Playwright is not installed.

These tests exist because API tests once passed while the page offered a Start button next to NOT DEPLOYED.
They check what a person sees and can click, in every state.
"""

import threading

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

    def open_(name):
        scenario = build(name)
        server = make_server(scenario.console, port=0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        context = browser.new_context(permissions=["clipboard-read", "clipboard-write"])
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(f"http://127.0.0.1:{server.server_address[1]}/")
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
    shown = page.locator("#controls button[data-action]")
    expect(shown).to_have_count(len(actions))
    assert sorted(shown.evaluate_all("els => els.map(e => e.dataset.action)")) == sorted(actions)


def test_nothing_on_any_page_mentions_a_cli_flag(open_scenario):
    for name in ("not-deployed", "probe-error"):
        page, _ = open_scenario(name)
        expect(page.locator("#verdict-text")).not_to_have_text("connecting…")
        assert "--flow-arn" not in page.locator("body").inner_text()


def test_not_deployed_explains_what_to_do(open_scenario):
    page, _ = open_scenario("not-deployed")

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

    expect(page.locator("#detail h2")).to_contain_text("SRT Input Source")
    expect(page.locator('#chain button[data-node="srt_source"]')).to_have_attribute("aria-pressed", "true")
    expect(page.locator('#chain button[data-node="srt_source"]')).to_have_attribute("data-health", "bad")


def test_a_failed_probe_shows_its_error_on_the_node(open_scenario):
    page, _ = open_scenario("probe-error")

    page.locator('#chain button[data-node="mediaconnect_flow"]').click()

    expect(page.locator("#detail .error")).to_contain_text("AccessDenied")


def test_the_player_frame_loads_the_deployed_player(open_scenario):
    page, _ = open_scenario("off-air")

    expect(page.locator("#player-frame")).to_have_attribute("src", OUTPUTS["player_url"])


def test_teardown_needs_the_typed_word_then_runs_terraform(open_scenario):
    page, scenario = open_scenario("off-air")

    page.locator('#controls button[data-action="teardown"]').click()
    expect(page.locator("#confirm")).to_be_visible()
    page.locator("#confirm-input").fill("yes")
    page.locator("#confirm-go").click()
    expect(page.locator("#banner")).to_contain_text("destroy")
    assert scenario.commands == []

    page.locator('#controls button[data-action="teardown"]').click()
    page.locator("#confirm-input").fill("destroy")
    page.locator("#confirm-go").click()
    page.locator('[data-tab="jobs"]').click()

    expect(page.locator("#log")).to_contain_text("terraform -chdir=envs/demo destroy")
    assert scenario.commands[0][2] == "destroy"


def test_a_poll_does_not_wipe_a_half_typed_confirmation(open_scenario):
    page, _ = open_scenario("off-air")
    page.locator('#controls button[data-action="teardown"]').click()
    page.locator("#confirm-input").fill("destr")

    page.wait_for_timeout(4500)  # two polls

    expect(page.locator("#confirm-input")).to_have_value("destr")


def test_the_log_panel_collapses(open_scenario):
    page, _ = open_scenario("off-air")

    page.locator("#panel-toggle").click()

    expect(page.locator("#panel")).to_be_hidden()
    expect(page.locator("#panel-toggle")).to_have_attribute("aria-expanded", "false")


def test_billing_states_show_a_rate(open_scenario):
    page, _ = open_scenario("partly-on")

    expect(page.locator("#cost")).to_contain_text("$0.29 / h")


def test_a_poll_does_not_take_focus_off_the_selected_node(open_scenario):
    page, _ = open_scenario("off-air")
    page.locator('#chain button[data-node="cloudfront_cdn"]').focus()

    page.wait_for_timeout(4500)

    assert page.evaluate("document.activeElement.dataset.node") == "cloudfront_cdn"


def test_the_mediaconnect_tab_reads_events_as_sentences(open_scenario):
    page, _ = open_scenario("off-air")

    page.locator('[data-tab="mediaconnect"]').click()

    expect(page.locator("#log")).to_contain_text("MediaConnect · flow STANDBY → ACTIVE")


def test_the_srt_tab_shows_tr_101_290_flags_and_never_the_passphrase(open_scenario):
    page, _ = open_scenario("source-running")

    page.locator('[data-tab="srt"]').click()

    expect(page.locator("#log")).to_contain_text("TR 101 290: continuity_count_error")
    expect(page.locator("#log")).to_contain_text("passphrase=***")


def test_selecting_a_node_opens_its_log_tab(open_scenario):
    page, _ = open_scenario("off-air")

    page.locator('#chain button[data-node="medialive_channel"]').click()

    expect(page.locator('[data-tab="medialive"]')).to_have_attribute("aria-selected", "true")
    expect(page.locator("#log")).to_contain_text("MediaLive · channel RUNNING")
    expect(page.locator("#log")).to_contain_text("has not written channel logs yet")


def test_a_raw_event_opens_on_click(open_scenario):
    page, _ = open_scenario("off-air")
    page.locator('[data-tab="all"]').click()

    page.locator("#log .has-raw").first.click()

    expect(page.locator("#log .raw").first).to_contain_text('"detail-type"')


def test_mediapackage_says_plainly_that_it_has_no_access_logs(open_scenario):
    page, _ = open_scenario("off-air")

    page.locator('[data-tab="mediapackage"]').click()

    expect(page.locator("#log")).to_contain_text("access logs are not enabled")
