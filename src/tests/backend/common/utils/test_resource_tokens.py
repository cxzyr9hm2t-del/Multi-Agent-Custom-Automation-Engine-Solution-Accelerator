"""Unit tests for backend.common.utils.resource_tokens.

These tokens are what let <img> and WebSocket requests authenticate without a
header, so the negative cases matter more than the happy path.
"""

import os
import sys
import time
from unittest.mock import patch

import pytest

src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
if src_path not in sys.path:
    sys.path.insert(0, src_path)

os.environ.setdefault('APP_ENV', 'dev')

from backend.common.utils.resource_tokens import (  # noqa: E402
    PURPOSE_IMAGE,
    PURPOSE_SOCKET,
    ResourceTokenError,
    mint,
    verify,
)


class TestRoundTrip:
    def test_a_minted_token_verifies_and_returns_its_user(self):
        token = mint(PURPOSE_SOCKET, "plan-1", "alice", 60)
        assert verify(token, PURPOSE_SOCKET, "plan-1") == "alice"

    def test_subject_may_be_empty_for_a_non_resource_grant(self):
        token = mint(PURPOSE_IMAGE, "", "alice", 60)
        assert verify(token, PURPOSE_IMAGE, "") == "alice"

    def test_minting_without_a_user_is_refused(self):
        with pytest.raises(ResourceTokenError):
            mint(PURPOSE_SOCKET, "plan-1", "", 60)


class TestRejection:
    def test_a_token_for_another_plan_is_rejected(self):
        """The whole point of binding the subject."""
        token = mint(PURPOSE_SOCKET, "my-plan", "alice", 60)
        with pytest.raises(ResourceTokenError, match="different resource"):
            verify(token, PURPOSE_SOCKET, "someone-elses-plan")

    def test_a_token_cannot_be_replayed_against_another_purpose(self):
        """An image token must not open a WebSocket."""
        token = mint(PURPOSE_IMAGE, "", "alice", 60)
        with pytest.raises(ResourceTokenError, match="different purpose"):
            verify(token, PURPOSE_SOCKET, "")

    def test_an_expired_token_is_rejected(self):
        token = mint(PURPOSE_SOCKET, "plan-1", "alice", 60)
        with patch("time.time", return_value=time.time() + 3600):
            with pytest.raises(ResourceTokenError, match="expired"):
                verify(token, PURPOSE_SOCKET, "plan-1")

    def test_a_tampered_payload_is_rejected(self):
        """Re-encoding the payload with a different user must not verify."""
        import base64
        import json

        token = mint(PURPOSE_SOCKET, "plan-1", "alice", 60)
        _, signature = token.split(".", 1)

        forged_payload = {
            "p": PURPOSE_SOCKET, "s": "plan-1", "u": "mallory",
            "e": int(time.time()) + 60,
        }
        forged = base64.urlsafe_b64encode(
            json.dumps(forged_payload, separators=(",", ":")).encode()
        ).decode().rstrip("=")

        with pytest.raises(ResourceTokenError, match="signature"):
            verify(f"{forged}.{signature}", PURPOSE_SOCKET, "plan-1")

    def test_a_tampered_signature_is_rejected(self):
        token = mint(PURPOSE_SOCKET, "plan-1", "alice", 60)
        encoded, _ = token.split(".", 1)
        with pytest.raises(ResourceTokenError):
            verify(f"{encoded}.AAAA", PURPOSE_SOCKET, "plan-1")

    @pytest.mark.parametrize("bad", ["", "no-dot", "...", "!!!.???"])
    def test_missing_or_malformed_tokens_are_rejected(self, bad):
        with pytest.raises(ResourceTokenError):
            verify(bad, PURPOSE_SOCKET, "plan-1")


class TestSigningKey:
    def test_a_configured_key_is_used_and_is_stable(self):
        import backend.common.utils.resource_tokens as rt

        with patch.object(rt.config, "API_TOKEN_SIGNING_KEY", "a-configured-key"):
            token = mint(PURPOSE_SOCKET, "plan-1", "alice", 60)
            assert verify(token, PURPOSE_SOCKET, "plan-1") == "alice"

    def test_a_token_does_not_verify_under_a_different_key(self):
        """A rotated or mismatched key invalidates outstanding tokens."""
        import backend.common.utils.resource_tokens as rt

        with patch.object(rt.config, "API_TOKEN_SIGNING_KEY", "key-one"):
            token = mint(PURPOSE_SOCKET, "plan-1", "alice", 60)

        with patch.object(rt.config, "API_TOKEN_SIGNING_KEY", "key-two"):
            with pytest.raises(ResourceTokenError):
                verify(token, PURPOSE_SOCKET, "plan-1")
