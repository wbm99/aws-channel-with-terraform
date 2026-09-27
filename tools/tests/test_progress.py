from livectl.progress import job_progress

# Real Terraform output shapes (-no-color), abbreviated.
APPLY = [
    "$ terraform -chdir=envs/demo apply -auto-approve -input=false -no-color",
    "module.ingest.random_password.srt: Refreshing state... [id=none]",
    "Terraform will perform the following actions:",
    "Plan: 4 to add, 1 to change, 0 to destroy.",
    "module.ingest.aws_iam_role.flow: Creating...",
    "module.ingest.aws_iam_role.flow: Creation complete after 1s [id=live-sports-aws-demo-mediaconnect]",
    "module.delivery.aws_cloudfront_distribution.this: Still creating... [00m10s elapsed]",
    "module.encode.aws_medialive_channel.this: Modifications complete after 3s [id=2286873]",
]


def test_before_the_plan_is_known_terraform_is_planning():
    progress = job_progress("deploy", APPLY[:2], "running")

    assert progress == {"done": 0, "total": None, "percent": 0, "label": "planning"}


def test_apply_counts_completed_resources_against_the_plan():
    progress = job_progress("deploy", APPLY, "running")

    assert progress == {"done": 2, "total": 5, "percent": 40, "label": "2 of 5 resources"}


def test_destroy_counts_destructions():
    lines = ["Plan: 0 to add, 0 to change, 3 to destroy.",
             "module.ingest.aws_secretsmanager_secret.srt: Destruction complete after 0s",
             "module.ingest.aws_iam_role.flow: Destruction complete after 1s"]

    assert job_progress("teardown", lines, "running")["percent"] == 66


def test_a_plan_with_nothing_to_do_is_complete():
    lines = ["No changes. Your infrastructure matches the configuration."]

    assert job_progress("deploy", lines, "running")["percent"] == 100


def test_a_finished_job_is_at_100_whatever_the_count():
    assert job_progress("deploy", APPLY, "succeeded")["percent"] == 100


def test_a_failed_job_keeps_the_progress_it_reached():
    assert job_progress("deploy", APPLY, "failed")["percent"] == 40


def test_going_live_counts_its_two_milestones():
    lines = ["starting the MediaConnect flow", "MediaConnect flow STARTING", "MediaConnect flow ACTIVE",
             "starting the MediaLive channel", "MediaLive channel STARTING"]

    assert job_progress("go-live", lines, "running") == {
        "done": 1, "total": 2, "percent": 50, "label": "MediaConnect flow active"}


def test_going_off_air_counts_its_two_milestones():
    lines = ["test source stopped", "stopping the MediaLive channel", "MediaLive channel IDLE",
             "stopping the MediaConnect flow", "MediaConnect flow STANDBY"]

    assert job_progress("go-off-air", lines, "running")["label"] == "MediaConnect flow in standby"
    assert job_progress("go-off-air", lines, "running")["percent"] == 100


def test_jobs_without_a_measure_have_no_progress():
    assert job_progress("scan", ["clean: nothing left"], "running") is None


def test_a_finished_job_never_says_it_is_still_planning():
    assert job_progress("teardown", ["$ terraform destroy"], "succeeded")["label"] == "done"
    assert job_progress("teardown", ["$ terraform destroy"], "failed")["label"] == "stopped before planning"
