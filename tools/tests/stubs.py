"""Small stand-ins for AWS clients.

moto has no MediaLive alerts and no MediaPackage v2 origin endpoints, and the console's scenario fixtures need
exact states (a running channel with no source, a failing call) that are awkward to reach through moto. A stub
answers each operation from a dict and records every call.
"""

from __future__ import annotations

from typing import Any, Optional

from botocore.exceptions import ClientError, NoCredentialsError

FLOW_ARN = "arn:aws:mediaconnect:us-east-1:123456789012:flow:1-abc:live-sports-aws-demo"
CHANNEL_ID = "7387208"


class StubClient:
    def __init__(self, **responses: Any) -> None:
        self._responses = responses
        self.calls: list[tuple[str, dict]] = []

    def __getattr__(self, name: str):
        if name.startswith("_") or name not in self._responses:
            raise AttributeError(f"stub has no operation {name!r}")

        def operation(**kwargs):
            self.calls.append((name, kwargs))
            answer = self._responses[name]
            if isinstance(answer, Exception):
                raise answer
            return answer(**kwargs) if callable(answer) else answer

        return operation


def metric_response(values: dict[str, Optional[float]]) -> dict:
    """A GetMetricData response with one newest-first value per query id (None means no datapoints)."""
    return {
        "MetricDataResults": [
            {"Id": key, "Values": [] if value is None else [value], "Timestamps": [], "StatusCode": "Complete"}
            for key, value in values.items()
        ]
    }


def stub_aws(
    *,
    flow: str = "STANDBY",
    channel: str = "IDLE",
    input_state: str = "ATTACHED",
    alerts: tuple = (),
    metrics: Optional[dict] = None,
    distribution: str = "Deployed",
    fail: tuple = (),
) -> dict[str, StubClient]:
    """Clients describing one state of the whole chain. `fail` names operations that raise."""
    error = RuntimeError("AccessDeniedException: stubbed failure")

    def maybe(name: str, answer: Any) -> Any:
        return error if name in fail else answer

    return {
        "mediaconnect": StubClient(
            describe_flow=maybe("describe_flow", {"Flow": {
                "FlowArn": FLOW_ARN, "Name": "live-sports-aws-demo", "Status": flow,
                "Source": {"IngestIp": "203.0.113.20", "IngestPort": 5000, "Transport": {"Protocol": "srt-listener"}},
            }}),
        ),
        "medialive": StubClient(
            describe_input=maybe("describe_input", {"Id": "4412345", "State": input_state, "Type": "MEDIACONNECT"}),
            describe_channel=maybe("describe_channel", {
                "Id": CHANNEL_ID, "Arn": f"arn:aws:medialive:us-east-1:123456789012:channel:{CHANNEL_ID}",
                "State": channel, "PipelinesRunningCount": 1 if channel == "RUNNING" else 0,
            }),
            list_alerts=maybe("list_alerts", {"Alerts": [
                {"AlertType": kind, "Message": message, "State": "SET"} for kind, message in alerts
            ]}),
        ),
        "mediapackagev2": StubClient(
            get_channel=maybe("get_channel", {"Arn": "arn:aws:mediapackagev2:::channel", "ChannelName": "live-sports-aws-demo"}),
            get_origin_endpoint=maybe("get_origin_endpoint", {"OriginEndpointName": "live-sports-aws-demo-hls"}),
        ),
        "cloudfront": StubClient(
            get_distribution=maybe("get_distribution", {"Distribution": {
                "Id": "E2EXAMPLE", "Status": distribution, "DomainName": "d1.cloudfront.net",
                "DistributionConfig": {"Enabled": True},
            }}),
        ),
        "cloudwatch": StubClient(get_metric_data=maybe("get_metric_data", metric_response(metrics or {}))),
    }


def logs_client(events: list[tuple[int, str]]) -> StubClient:
    """A CloudWatch Logs stub serving (timestamp, message) events from the events group.

    The ElementalMediaLive group does not exist, as on an account whose channel has never run.
    """

    def filter_log_events(logGroupName, startTime, filterPattern=None, **_):
        if logGroupName == "ElementalMediaLive":
            raise RuntimeError("ResourceNotFoundException: The specified log group does not exist.")
        # Enough of CloudWatch's `?"a" ?"b"` pattern for these fixtures: keep messages containing any quoted term.
        terms = [term.strip('?"') for term in filterPattern.split('" ?')] if filterPattern else []
        return {"events": [{"timestamp": t, "logStreamName": "s", "message": m} for t, m in events
                           if t >= startTime and (not terms or any(term in m for term in terms))]}

    return StubClient(filter_log_events=filter_log_events)


ACCOUNT = "123456789012"
STS_ANSWERS = {
    "ok": {"Account": ACCOUNT, "Arn": f"arn:aws:sts::{ACCOUNT}:assumed-role/LiveOps/william", "UserId": "AROA:william"},
    "root": {"Account": ACCOUNT, "Arn": f"arn:aws:iam::{ACCOUNT}:root", "UserId": ACCOUNT},
}


def sts_error(code: str) -> Exception:
    return ClientError({"Error": {"Code": code, "Message": f"stubbed {code}"}}, "GetCallerIdentity")


def sts_client(kind: str = "ok") -> StubClient:
    """An STS stub answering GetCallerIdentity as one identity kind (see livectl.identity)."""
    answer = {
        "no-credentials": NoCredentialsError(),
        "expired": sts_error("ExpiredToken"),
        "invalid": sts_error("InvalidClientTokenId"),
    }.get(kind) or STS_ANSWERS[kind]
    return StubClient(get_caller_identity=answer)
