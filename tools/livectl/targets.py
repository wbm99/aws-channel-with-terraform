"""Resolve which AWS resources livectl operates on."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

Runner = Callable[[Sequence[str]], str]


class TargetError(RuntimeError):
    """Raised when the target resources cannot be determined."""


@dataclass(frozen=True)
class Targets:
    flow_arn: str
    channel_id: str
    player_url: Optional[str] = None
    ingest_ip: Optional[str] = None
    ingest_port: Optional[int] = None


def run_command(args: Sequence[str]) -> str:
    """Run a command and return its standard output."""
    try:
        result = subprocess.run(args, check=True, capture_output=True, text=True)
    except FileNotFoundError as error:
        raise TargetError(f"command not found: {args[0]}") from error
    except subprocess.CalledProcessError as error:
        raise TargetError(f"{' '.join(args)} failed: {error.stderr.strip()}") from error
    return result.stdout


def read_terraform_outputs(tf_dir: str, runner: Runner = run_command) -> dict:
    """Return the Terraform outputs of tf_dir as a plain name-to-value dict."""
    raw = runner(["terraform", f"-chdir={tf_dir}", "output", "-json"])
    try:
        outputs = json.loads(raw)
    except json.JSONDecodeError as error:
        raise TargetError("terraform output did not return valid JSON") from error
    return {name: item["value"] for name, item in outputs.items()}


def resolve_targets(
    flow_arn: Optional[str],
    channel_id: Optional[str],
    tf_dir: str,
    runner: Runner = run_command,
) -> Targets:
    """Combine flags and Terraform outputs; flags win. Terraform is skipped if both flags are set."""
    outputs: dict = {}
    if not (flow_arn and channel_id):
        outputs = read_terraform_outputs(tf_dir, runner)

    flow = flow_arn or outputs.get("flow_arn")
    channel = channel_id or outputs.get("medialive_channel_id")
    missing = [label for label, value in (("flow ARN", flow), ("channel ID", channel)) if not value]
    if missing:
        raise TargetError(
            "could not determine " + " and ".join(missing)
            + "; pass --flow-arn/--channel-id or run against a deployed environment"
        )

    return Targets(
        flow_arn=flow,
        channel_id=str(channel),
        player_url=outputs.get("player_url"),
        ingest_ip=outputs.get("ingest_ip"),
        ingest_port=outputs.get("ingest_port"),
    )
