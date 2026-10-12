from livectl.actions import ACTIONS, Situation, next_action, refusals


def allowed(situation):
    return {name for name, refusal in refusals(situation).items() if refusal is None}


OFF_AIR = Situation(deployed=True, job_running=None, flow_state="STANDBY", channel_state="IDLE")
ON_AIR = Situation(deployed=True, job_running=None, flow_state="ACTIVE", channel_state="RUNNING")


def test_every_action_has_an_answer():
    assert set(refusals(OFF_AIR)) == set(ACTIONS)


def test_not_deployed_offers_deploy_teardown_and_scan():
    assert allowed(Situation(deployed=False, job_running=None, flow_state=None, channel_state=None)) == {
        "deploy", "teardown", "scan", "source-stop", "config"}


def test_off_air_offers_going_live_but_not_going_off_air():
    assert allowed(OFF_AIR) == {"deploy", "teardown", "scan", "go-live", "source-stop", "config"}


def test_on_air_refuses_deploy_and_teardown_and_offers_the_source():
    assert allowed(ON_AIR) == {"scan", "go-off-air", "source-start", "source-stop", "config", "ad-break"}
    assert "Go off air first" in refusals(ON_AIR)["teardown"].message
    assert refusals(ON_AIR)["deploy"].status == 409


def test_partly_on_offers_both_directions():
    partly = Situation(deployed=True, job_running=None, flow_state="ACTIVE", channel_state="IDLE")

    assert {"go-live", "go-off-air"} <= allowed(partly)


def test_a_running_job_blocks_everything_but_stopping_the_source():
    busy = Situation(deployed=True, job_running="go-live", flow_state="STANDBY", channel_state="IDLE")

    assert allowed(busy) == {"source-stop"}
    assert refusals(busy)["deploy"].message == "go-live is still running"


def test_a_running_test_source_blocks_teardown():
    situation = Situation(deployed=True, job_running=None, flow_state="STANDBY", channel_state="IDLE",
                          source_running=True)

    assert "Stop the test source first" in refusals(situation)["teardown"].message


def test_the_source_needs_an_active_flow():
    assert "flow is not active" in refusals(OFF_AIR)["source-start"].message


def test_unknown_states_do_not_block_teardown():
    unknown = Situation(deployed=True, job_running=None, flow_state=None, channel_state=None)

    assert "teardown" in allowed(unknown)


def test_messages_never_leak_cli_flags():
    everything = [OFF_AIR, ON_AIR, Situation(deployed=False, job_running=None, flow_state=None, channel_state=None)]
    for situation in everything:
        for refusal in refusals(situation).values():
            assert refusal is None or "--" not in refusal.message


# --- the recommended next step --------------------------------------------------

from livectl.actions import next_action  # noqa: E402


def test_with_nothing_deployed_the_next_step_is_deploy():
    assert next_action(Situation(deployed=False, job_running=None, flow_state=None, channel_state=None)) == "deploy"


def test_deployed_and_off_air_the_next_step_is_going_live():
    assert next_action(OFF_AIR) == "go-live"


def test_on_air_without_a_source_the_next_step_is_the_test_source():
    assert next_action(ON_AIR, source_connected=False) == "source-start"


def test_on_air_with_a_source_there_is_nothing_to_suggest():
    assert next_action(ON_AIR, source_connected=True) is None
    running = Situation(deployed=True, job_running=None, flow_state="ACTIVE", channel_state="RUNNING",
                        source_running=True)
    assert next_action(running, source_connected=False) is None, "it is starting; do not nag"


def test_nothing_is_suggested_while_a_job_runs_or_halfway_up():
    busy = Situation(deployed=True, job_running="go-live", flow_state="STANDBY", channel_state="IDLE")
    partly = Situation(deployed=True, job_running=None, flow_state="ACTIVE", channel_state="IDLE")

    assert next_action(busy) is None
    assert next_action(partly) is None


def test_without_credentials_every_aws_action_is_refused_with_the_reason():
    message = "No AWS credentials found. Configure a profile on the host."
    situation = Situation(deployed=False, job_running=None, flow_state=None, channel_state=None, credentials=message)
    answers = refusals(situation)

    for action in ACTIONS:
        if action in ("source-stop", "config"):  # stopping FFmpeg and saving a local file need no AWS
            assert answers[action] is None
        else:
            assert answers[action].status == 403 and answers[action].message == message, action


def test_without_credentials_no_step_is_suggested():
    situation = Situation(deployed=False, job_running=None, flow_state=None, channel_state=None,
                          credentials="No AWS credentials found.")

    assert next_action(situation) is None


def test_a_break_needs_a_running_channel():
    assert refusals(OFF_AIR)["ad-break"].message == "Go live first: breaks are cued on a running channel."
    partly = Situation(deployed=True, job_running=None, flow_state="ACTIVE", channel_state="IDLE")
    assert refusals(partly)["ad-break"].status == 409
    assert refusals(ON_AIR)["ad-break"] is None


def test_config_changes_wait_for_a_job():
    busy = Situation(deployed=True, job_running="deploy", flow_state="STANDBY", channel_state="IDLE")
    assert refusals(busy)["config"].message == "deploy is still running"
    assert refusals(busy)["config"].status == 409


def test_config_is_allowed_off_air_and_without_a_stack():
    nothing = Situation(deployed=False, job_running=None, flow_state=None, channel_state=None)
    assert refusals(nothing)["config"] is None
    assert refusals(OFF_AIR)["config"] is None
