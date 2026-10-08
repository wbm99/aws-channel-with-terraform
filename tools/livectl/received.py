"""What MediaConnect parsed from the incoming transport stream, for comparison with what the source sent.

`DescribeFlowSourceMetadata` reports each program (its name comes from the SDT service name) and each stream's
codec, resolution, frame rate, channels and sample rate. It does not report bitrate. The client is injected.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from livectl.source import now_ms


def _fps(text: Optional[str]) -> Optional[float]:
    """MediaConnect gives frame rates as "30" or "30000/1001"."""
    if not text:
        return None
    try:
        numerator, _, denominator = text.partition("/")
        return round(float(numerator) / float(denominator or 1), 2)
    except (ValueError, ZeroDivisionError):
        return None


def _stream(stream: dict) -> dict:
    resolution = stream.get("FrameResolution") or {}
    return {
        "type": (stream.get("StreamType") or "").lower() or None,
        "codec": stream.get("Codec"),
        "width": resolution.get("FrameWidth"),
        "height": resolution.get("FrameHeight"),
        "fps": _fps(stream.get("FrameRate")),
        "channels": stream.get("Channels"),
        "sample_rate": stream.get("SampleRate"),
    }


def received(mediaconnect: Any, flow_arn: str, *, clock_ms: Callable[[], int] = now_ms) -> dict:
    """What MediaConnect parsed from the incoming transport stream. No programs and no messages: nothing yet."""
    answer = mediaconnect.describe_flow_source_metadata(FlowArn=flow_arn)
    programs = (answer.get("TransportMediaInfo") or {}).get("Programs") or []
    return {
        "programs": [{"name": program.get("ProgramName"), "number": program.get("ProgramNumber"),
                      "streams": [_stream(stream) for stream in program.get("Streams") or []]}
                     for program in programs],
        "messages": [message.get("Message", "") for message in answer.get("Messages") or []],
        "at": clock_ms(),
    }
