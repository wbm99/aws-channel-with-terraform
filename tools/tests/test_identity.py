import boto3
import pytest
from botocore.exceptions import (
    EndpointConnectionError,
    SSOTokenLoadError,
    TokenRetrievalError,
    UnauthorizedSSOTokenError,
)

from livectl.identity import whoami
from stubs import StubClient, sts_client, sts_error


def test_moto_credentials_are_a_plain_user(aws):
    identity = whoami(boto3.client("sts"), "us-east-1")

    assert identity.kind == "ok" and identity.usable
    assert identity.label == "acting as user moto · account 123456789012 · us-east-1"
    assert identity.message is None


def test_an_assumed_role_shows_the_role_and_the_session():
    identity = whoami(sts_client("ok"), "us-east-1")

    assert identity.kind == "ok"
    assert identity.label == "acting as role LiveOps (william) · account 123456789012 · us-east-1"


def test_root_is_usable_but_warned_about():
    identity = whoami(sts_client("root"), "us-east-1")

    assert identity.kind == "root" and identity.usable
    assert identity.label == "acting as root · account 123456789012 · us-east-1"
    assert identity.message.startswith("You are using the account's root user")


def test_no_credentials_says_how_to_configure_a_profile():
    identity = whoami(sts_client("no-credentials"), "us-east-1")

    assert identity.kind == "no-credentials" and not identity.usable
    assert identity.verdict == "No AWS credentials"
    assert "aws configure" in identity.message
    # botocore fixes a client's credentials when it is built: new ones need a new console, not a reload.
    assert "restart the console" in identity.message and "reload" not in identity.message


def test_an_expired_session_names_the_profile_to_log_in_with():
    identity = whoami(sts_client("expired"), "us-east-1", profile="demo")

    assert identity.kind == "expired" and not identity.usable
    assert identity.verdict == "AWS session expired"
    assert 'aws sso login --profile demo' in identity.message


def test_an_expired_session_without_a_profile_does_not_invent_one():
    identity = whoami(sts_client("expired"), "us-east-1")

    assert "aws sso login" in identity.message and "--profile" not in identity.message


def test_rejected_keys_are_invalid():
    identity = whoami(sts_client("invalid"), "us-east-1")

    assert identity.kind == "invalid" and not identity.usable
    assert identity.verdict == "AWS credentials rejected"
    assert "restart the console" in identity.message


@pytest.mark.parametrize("error", [
    sts_error("ExpiredToken"),
    sts_error("ExpiredTokenException"),
    TokenRetrievalError(provider="sso", error_msg="expired"),
    UnauthorizedSSOTokenError(),
    SSOTokenLoadError(error_msg="no cached token"),
])
def test_every_expired_shape_is_expired(error):
    assert whoami(StubClient(get_caller_identity=error), "us-east-1").kind == "expired"


@pytest.mark.parametrize("code", ["InvalidClientTokenId", "SignatureDoesNotMatch", "UnrecognizedClientException"])
def test_every_rejected_key_shape_is_invalid(code):
    assert whoami(StubClient(get_caller_identity=sts_error(code)), "us-east-1").kind == "invalid"


def test_an_unreachable_sts_does_not_block_the_console():
    identity = whoami(StubClient(get_caller_identity=EndpointConnectionError(endpoint_url="https://sts")), "us-east-1")

    assert identity.kind == "unverified" and identity.usable
    assert identity.label == "identity not verified · us-east-1"
    assert identity.message is None


def test_the_payload_form_carries_what_the_page_needs():
    payload = whoami(sts_client("root"), "us-east-1").to_dict()

    assert payload["kind"] == "root" and payload["usable"] is True
    assert payload["label"].startswith("acting as root")
    assert payload["account"] == "123456789012"
