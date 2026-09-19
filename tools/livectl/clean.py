"""Find billable resources that were left behind."""

from __future__ import annotations

# A channel that is being or has been deleted is not a leftover and is not billed.
GONE_STATES = {"DELETING", "DELETED"}


def _pages(client, operation: str, **kwargs):
    yield from client.get_paginator(operation).paginate(**kwargs)


def find_leftovers(mediaconnect, medialive, mediapackagev2, cloudfront, prefix: str) -> list[str]:
    """Return one line per resource whose name (or comment) starts with prefix."""
    found: list[str] = []

    for page in _pages(mediaconnect, "list_flows"):
        for flow in page.get("Flows", []):
            if flow["Name"].startswith(prefix):
                found.append(f"MediaConnect flow {flow['Name']} ({flow['Status']})")

    for page in _pages(medialive, "list_channels"):
        for channel in page.get("Channels", []):
            if channel["Name"].startswith(prefix) and channel["State"] not in GONE_STATES:
                found.append(f"MediaLive channel {channel['Name']} ({channel['State']})")

    for page in _pages(medialive, "list_inputs"):
        for item in page.get("Inputs", []):
            if item["Name"].startswith(prefix):
                found.append(f"MediaLive input {item['Name']}")

    for page in _pages(mediapackagev2, "list_channel_groups"):
        for group in page.get("Items", []):
            if group["ChannelGroupName"].startswith(prefix):
                found.append(f"MediaPackage channel group {group['ChannelGroupName']}")

    for page in _pages(cloudfront, "list_distributions"):
        for distribution in page.get("DistributionList", {}).get("Items", []):
            if distribution.get("Comment", "").startswith(prefix):
                found.append(f"CloudFront distribution {distribution['Id']}")

    return found
