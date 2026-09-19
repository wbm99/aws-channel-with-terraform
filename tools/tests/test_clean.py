import boto3

from conftest import make_workflow
from livectl.clean import find_leftovers


def clients():
    return (
        boto3.client("mediaconnect"),
        boto3.client("medialive"),
        boto3.client("mediapackagev2"),
        boto3.client("cloudfront"),
    )


def test_a_clean_account_reports_nothing(aws):
    assert find_leftovers(*clients(), prefix="live-sports-aws") == []


def test_leftover_flow_and_channel_are_reported(aws):
    make_workflow("live-sports-aws-demo")

    leftovers = find_leftovers(*clients(), prefix="live-sports-aws")

    assert len(leftovers) == 2
    assert any("MediaConnect flow live-sports-aws-demo" in item for item in leftovers)
    assert any("MediaLive channel live-sports-aws-demo" in item for item in leftovers)


def test_resources_with_other_names_are_ignored(aws):
    make_workflow("someone-elses-stream")

    assert find_leftovers(*clients(), prefix="live-sports-aws") == []


def test_deleted_resources_disappear(aws):
    targets = make_workflow("live-sports-aws-demo")
    mediaconnect, medialive, *_ = clients()
    medialive.delete_channel(ChannelId=targets.channel_id)
    mediaconnect.delete_flow(FlowArn=targets.flow_arn)

    assert find_leftovers(*clients(), prefix="live-sports-aws") == []


def test_mediapackage_channel_groups_are_reported(aws):
    boto3.client("mediapackagev2").create_channel_group(ChannelGroupName="live-sports-aws-demo")

    leftovers = find_leftovers(*clients(), prefix="live-sports-aws")

    assert leftovers == ["MediaPackage channel group live-sports-aws-demo"]


def test_a_channel_that_is_being_deleted_is_not_a_leftover(aws):
    targets = make_workflow("live-sports-aws-demo")
    boto3.client("medialive").delete_channel(ChannelId=targets.channel_id)

    leftovers = find_leftovers(*clients(), prefix="live-sports-aws")

    assert not any("MediaLive channel" in item for item in leftovers)
