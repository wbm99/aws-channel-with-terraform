"""Command-line entry point for livectl."""

from __future__ import annotations

import argparse
import sys
from typing import Optional, Sequence

import boto3

from livectl.clean import find_informational, find_leftovers
from livectl.control import WaitTimeout, start, stop
from livectl.server import LOOPBACK, Console, ConsoleError, serve
from livectl.status import get_status
from livectl.targets import Runner, TargetError, resolve_targets, run_command


def _region_parent() -> argparse.ArgumentParser:
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument("--region", default="us-east-1", help="AWS region (default: us-east-1)")
    return parent


def _target_parent() -> argparse.ArgumentParser:
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument("--tf-dir", default="envs/demo", help="Terraform environment to read outputs from")
    parent.add_argument("--flow-arn", help="MediaConnect flow ARN (overrides Terraform output)")
    parent.add_argument("--channel-id", help="MediaLive channel ID (overrides Terraform output)")
    return parent


def _loopback(value: str) -> str:
    """Reject anything but a loopback address: the console can destroy infrastructure."""
    if value not in LOOPBACK:
        raise argparse.ArgumentTypeError(f"the console binds to loopback only, not {value!r}")
    return value


def _wait_parent() -> argparse.ArgumentParser:
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument("--timeout", type=float, default=600, help="seconds to wait for each state change")
    parent.add_argument("--interval", type=float, default=5, help="seconds between polls")
    return parent


def build_parser() -> argparse.ArgumentParser:
    region, target, wait = _region_parent(), _target_parent(), _wait_parent()
    parser = argparse.ArgumentParser(prog="livectl", description="Operate the live SRT to HLS demo pipeline.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("start", parents=[region, target, wait], help="start the flow, then the channel")
    commands.add_parser("stop", parents=[region, target, wait], help="stop the channel, then the flow")
    commands.add_parser("status", parents=[region, target], help="show flow, channel and source state")
    clean = commands.add_parser("check-clean", parents=[region], help="fail if billable resources are left")
    clean.add_argument("--prefix", default="live-sports-aws", help="name prefix of the resources to look for")
    ui = commands.add_parser("ui", parents=[region, target], help="serve the operations console on localhost")
    ui.add_argument("--port", type=int, default=8765, help="port to listen on (default: 8765)")
    ui.add_argument("--host", type=_loopback, default="127.0.0.1", help="loopback address to bind")
    ui.add_argument("--no-browser", action="store_true", help="do not open a browser window")
    ui.add_argument("--prefix", default="live-sports-aws", help="name prefix used by the clean check")
    return parser


def _print_status(status: dict) -> None:
    connected = {True: "connected", False: "not connected", None: "unknown"}[status["source_connected"]]
    print(f"flow:      {status['flow']}")
    print(f"channel:   {status['channel']}")
    print(f"source:    {connected}")
    if status["ingest"]:
        print(f"ingest:    {status['ingest']}")
    if status["player"]:
        print(f"player:    {status['player']}")


def main(argv: Optional[Sequence[str]] = None, *, runner: Runner = run_command) -> int:
    args = build_parser().parse_args(argv)
    session = boto3.Session(region_name=args.region)

    try:
        if args.command == "check-clean":
            leftovers = find_leftovers(
                session.client("mediaconnect"),
                session.client("medialive"),
                session.client("mediapackagev2"),
                session.client("cloudfront"),
                args.prefix,
            )
            info = find_informational(session.client("logs"), args.prefix)
            if leftovers:
                print("Billable resources still exist:")
                for line in leftovers:
                    print(f"  {line}")
            else:
                print("clean: nothing left")
            for line in info:
                print(f"info: {line}")
            return 1 if leftovers else 0

        if args.command == "ui":
            # Targets are resolved lazily, so the console still starts with nothing deployed.
            serve(
                Console(
                    mediaconnect=session.client("mediaconnect"),
                    medialive=session.client("medialive"),
                    cloudwatch=session.client("cloudwatch"),
                    mediapackagev2=session.client("mediapackagev2"),
                    cloudfront=session.client("cloudfront"),
                    tf_dir=args.tf_dir,
                    prefix=args.prefix,
                    runner=runner,
                    flow_arn=args.flow_arn,
                    channel_id=args.channel_id,
                    region=args.region,
                ),
                host=args.host,
                port=args.port,
                open_browser=not args.no_browser,
            )
            return 0

        targets = resolve_targets(args.flow_arn, args.channel_id, args.tf_dir, runner)
        mediaconnect, medialive = session.client("mediaconnect"), session.client("medialive")

        if args.command == "status":
            _print_status(get_status(mediaconnect, medialive, session.client("cloudwatch"), targets))
        elif args.command == "start":
            start(mediaconnect, medialive, targets, log=print, timeout=args.timeout, interval=args.interval)
        elif args.command == "stop":
            stop(mediaconnect, medialive, targets, log=print, timeout=args.timeout, interval=args.interval)
        return 0
    except (TargetError, WaitTimeout, ConsoleError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
