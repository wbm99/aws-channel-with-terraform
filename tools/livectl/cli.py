"""Command-line entry point for livectl."""

from __future__ import annotations

import argparse
import atexit
import os
import sys
from typing import Optional, Sequence

import boto3
from botocore.config import Config
from botocore.exceptions import ProfileNotFound

from livectl.clean import find_informational, find_leftovers
from livectl.control import WaitTimeout, start, stop
from livectl.identity import Identity, whoami
from livectl.server import Console, ConsoleError, allowed_hosts, serve
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
    ui.add_argument("--host", default="127.0.0.1", help="loopback address to bind")
    ui.add_argument("--container", action="store_true",
                    help="running in a container whose published port is bound to the host's loopback; "
                         "allows --host 0.0.0.0")
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


class NoIdentity(RuntimeError):
    """The AWS credentials cannot be used; the message says how to fix them."""


def _identity_line(session: boto3.Session, region: str) -> Identity:
    """Print who the command acts as, first, or stop when there is nobody to act as."""
    identity = whoami(session.client("sts"), region, session.profile_name)
    if not identity.usable:
        raise NoIdentity(identity.message)
    print(identity.label)
    return identity


def main(argv: Optional[Sequence[str]] = None, *, runner: Runner = run_command) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # The console can destroy infrastructure: loopback only, or every interface inside a container (see server.py).
    if args.command == "ui" and args.host not in allowed_hosts(args.container):
        parser.error(f"the console binds to loopback only, not {args.host!r}"
                     + ("" if args.container else " (0.0.0.0 is allowed only with --container)"))
    try:
        session = boto3.Session(region_name=args.region)
        if args.command == "check-clean":
            _identity_line(session, args.region)
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
            # Short timeouts and few retries: with the defaults (60 s connect, 60 s read, several retries) a dropped
            # network kept every poll blocked for minutes. The console would rather show stale data and say so.
            session_config = Config(connect_timeout=3, read_timeout=8, retries={"max_attempts": 2, "mode": "standard"})
            client = lambda name: session.client(name, config=session_config)  # noqa: E731
            # Targets are resolved lazily, so the console still starts with nothing deployed.
            console = Console(
                mediaconnect=client("mediaconnect"),
                medialive=client("medialive"),
                cloudwatch=client("cloudwatch"),
                mediapackagev2=client("mediapackagev2"),
                cloudfront=client("cloudfront"),
                tf_dir=args.tf_dir,
                prefix=args.prefix,
                runner=runner,
                flow_arn=args.flow_arn,
                channel_id=args.channel_id,
                region=args.region,
                logs=client("logs"),
                sts=client("sts"),
                profile=session.profile_name,
                # Read only when "Send test source" is clicked, and handed straight to the child's environment.
                read_passphrase=lambda arn: client("secretsmanager").get_secret_value(
                    SecretId=arn)["SecretString"],
            )
            atexit.register(console.source.stop)
            # Inside a container the address people open is the host's published one, which compose passes in.
            serve(console, host=args.host, port=args.port, open_browser=not args.no_browser,
                  container=args.container, url=os.environ.get("CONSOLE_URL") if args.container else None)
            return 0

        if args.command == "status":  # before the targets: `terraform output` reads the S3 state with the same keys
            _identity_line(session, args.region)
        targets = resolve_targets(args.flow_arn, args.channel_id, args.tf_dir, runner)
        mediaconnect, medialive = session.client("mediaconnect"), session.client("medialive")

        if args.command == "status":
            _print_status(get_status(mediaconnect, medialive, session.client("cloudwatch"), targets))
        elif args.command == "start":
            start(mediaconnect, medialive, targets, log=print, timeout=args.timeout, interval=args.interval)
        elif args.command == "stop":
            stop(mediaconnect, medialive, targets, log=print, timeout=args.timeout, interval=args.interval)
        return 0
    except ProfileNotFound as error:
        name = error.kwargs.get("profile", "")
        print(f"error: AWS profile '{name}' is not in ~/.aws/config. Set AWS_PROFILE to one that is, or create it: "
              f"aws configure --profile {name}", file=sys.stderr)
        return 2
    except (TargetError, WaitTimeout, ConsoleError, NoIdentity) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
