"""Who the tool acts as in AWS, and what to do when it cannot tell.

`sts:GetCallerIdentity` needs no IAM permission at all, so it separates credential problems (none found, an expired
SSO session, rejected keys) from permission problems, which show up later as named denials on the nodes. The page
shows the answer in its header, so nobody deploys into the wrong account, and warns about the root user.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional

from botocore.exceptions import (
    ClientError,
    NoCredentialsError,
    SSOTokenLoadError,
    TokenRetrievalError,
    UnauthorizedSSOTokenError,
)

IDENTITY_TTL = 15.0
USABLE = {"ok", "root", "unverified"}
EXPIRED_CODES = {"ExpiredToken", "ExpiredTokenException"}
INVALID_CODES = {"InvalidClientTokenId", "SignatureDoesNotMatch", "UnrecognizedClientException"}
VERDICTS = {"no-credentials": "No AWS credentials", "expired": "AWS session expired",
            "invalid": "AWS credentials rejected"}

ROOT_MESSAGE = ("You are using the account's root user. Create an IAM user or role for this project: "
                "root cannot be restricted by any policy.")
NO_CREDENTIALS_MESSAGE = ("No AWS credentials found. Configure a profile on the host (aws configure, or aws "
                          "configure sso), set AWS_PROFILE, then reload.")
INVALID_MESSAGE = "AWS rejected these credentials: the access key is wrong or has been deactivated."


def _expired_message(profile: Optional[str]) -> str:
    login = f"aws sso login --profile {profile}" if profile else "aws sso login"
    return f'Your AWS session has expired. Run "{login}" on the host; the console picks it up within 15 seconds.'


@dataclass(frozen=True)
class Identity:
    kind: str  # ok | root | unverified | no-credentials | expired | invalid
    region: str
    account: Optional[str] = None
    arn: Optional[str] = None
    message: Optional[str] = None  # banner text; None when there is nothing to warn about

    @property
    def usable(self) -> bool:
        return self.kind in USABLE

    @property
    def label(self) -> str:
        if self.account is None:
            return f"identity not verified · {self.region}"
        return f"acting as {principal(self.arn)} · account {self.account} · {self.region}"

    @property
    def verdict(self) -> str:
        return VERDICTS.get(self.kind, "")

    def to_dict(self) -> dict:
        return {**asdict(self), "usable": self.usable, "label": self.label}


def principal(arn: str) -> str:
    """`root`, `user <name>`, `role <role> (<session>)`, or the ARN itself."""
    resource = arn.split(":", 5)[-1]
    if resource == "root":
        return "root"
    kind, _, rest = resource.partition("/")
    if kind == "user":
        return f"user {rest.rsplit('/', 1)[-1]}"
    if kind == "assumed-role" and "/" in rest:
        role, session = rest.rsplit("/", 1)
        return f"role {role} ({session})"
    return arn


def whoami(sts, region: str, profile: Optional[str] = None) -> Identity:
    """Classify the credentials `sts` signs with. Never raises: anything unexpected is `unverified` and usable."""
    try:
        answer = sts.get_caller_identity()
    except NoCredentialsError:
        return Identity("no-credentials", region, message=NO_CREDENTIALS_MESSAGE)
    except (TokenRetrievalError, UnauthorizedSSOTokenError, SSOTokenLoadError):
        return Identity("expired", region, message=_expired_message(profile))
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code")
        if code in EXPIRED_CODES:
            return Identity("expired", region, message=_expired_message(profile))
        if code in INVALID_CODES:
            return Identity("invalid", region, message=INVALID_MESSAGE)
        return Identity("unverified", region)
    except Exception:  # network down, throttled: the stale-data handling already says so
        return Identity("unverified", region)
    arn = answer["Arn"]
    if principal(arn) == "root":
        return Identity("root", region, answer["Account"], arn, ROOT_MESSAGE)
    return Identity("ok", region, answer["Account"], arn)
