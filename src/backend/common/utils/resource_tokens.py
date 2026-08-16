"""Short-lived signed tokens for requests that cannot carry a header.

Two things in this application are fetched by the browser in a way that cannot
carry an ``Authorization`` header: generated images, loaded through a plain
``<img src>`` in the markdown renderer, and the WebSocket, because browsers
cannot set headers on a handshake. Behind the authenticating front door
(``docs/backend_api_authentication.md``) both would simply 401.

A token minted here travels in the query string instead. It is bound to a
purpose, a subject and a user, and it expires, so possessing one grants exactly
one kind of access to one resource for a short time.

The signing key comes from ``API_TOKEN_SIGNING_KEY`` when configured. When it is
not, a random key is generated once per process — which is sound here because
the backend runs at a single replica by design (see OrchestrationConfig), so
there is no second instance to disagree with. The cost is that a restart
invalidates outstanding tokens; they are short-lived and the frontend re-mints
on demand, so that is a refresh at worst. Configure the key explicitly if you
ever lift the single-replica constraint.
"""

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time

from common.config.app_config import config

logger = logging.getLogger(__name__)

# Purposes are part of the signed payload, so a token minted for one kind of
# resource cannot be replayed against another.
PURPOSE_SOCKET = "socket"
PURPOSE_IMAGE = "image"
PURPOSE_CLARIFY = "clarify"

DEFAULT_SOCKET_TTL_SECONDS = 120
DEFAULT_IMAGE_TTL_SECONDS = 900
# A clarify token lives as long as a plausible orchestration: it is minted when
# the agent is built and must still verify when that agent asks its question,
# which may be many turns later.
DEFAULT_CLARIFY_TTL_SECONDS = 3600

_process_signing_key: bytes | None = None


class ResourceTokenError(Exception):
    """A token was missing, malformed, expired, or not valid for this request."""


def _signing_key() -> bytes:
    """Return the HMAC signing key, generating a per-process one if unset."""
    global _process_signing_key

    configured = getattr(config, "API_TOKEN_SIGNING_KEY", "")
    # Require a real string: config is replaced by a mock in parts of the test
    # suite, and a stand-in object is truthy but produces no usable key bytes.
    if isinstance(configured, str) and configured:
        return configured.encode("utf-8")

    if _process_signing_key is None:
        _process_signing_key = secrets.token_bytes(32)
        logger.info(
            "API_TOKEN_SIGNING_KEY is not set; using a per-process signing key. "
            "Resource tokens will not survive a restart."
        )
    return _process_signing_key


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def mint(purpose: str, subject: str, user_id: str, ttl_seconds: int) -> str:
    """Create a token granting `user_id` access to `subject` for `purpose`.

    Args:
        purpose: PURPOSE_SOCKET or PURPOSE_IMAGE — replay across purposes fails.
        subject: The resource the token is for (a plan id, a blob name). Use ""
            when the grant is not tied to one specific resource.
        user_id: The authenticated principal the token is issued to.
        ttl_seconds: How long the token remains valid.
    """
    if not user_id:
        raise ResourceTokenError("Cannot mint a token without a user")

    payload = {
        "p": purpose,
        "s": subject,
        "u": user_id,
        "e": int(time.time()) + ttl_seconds,
    }
    encoded = _b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(
        _signing_key(), encoded.encode("ascii"), hashlib.sha256
    ).digest()
    return f"{encoded}.{_b64encode(signature)}"


def verify(token: str, purpose: str, subject: str) -> str:
    """Validate a token and return the user it was issued to.

    Raises:
        ResourceTokenError: if the token is missing, malformed, has been
            tampered with, has expired, or was issued for a different purpose or
            subject.
    """
    if not token:
        raise ResourceTokenError("No token supplied")

    try:
        encoded, provided_signature = token.split(".", 1)
    except ValueError as exc:
        raise ResourceTokenError("Malformed token") from exc

    expected = hmac.new(
        _signing_key(), encoded.encode("ascii"), hashlib.sha256
    ).digest()
    try:
        provided = _b64decode(provided_signature)
    except Exception as exc:
        raise ResourceTokenError("Malformed token signature") from exc

    # Constant-time: a timing-variable comparison here would leak the signature.
    if not hmac.compare_digest(expected, provided):
        raise ResourceTokenError("Token signature does not match")

    try:
        payload = json.loads(_b64decode(encoded).decode("utf-8"))
    except Exception as exc:
        raise ResourceTokenError("Malformed token payload") from exc

    if payload.get("p") != purpose:
        raise ResourceTokenError("Token was issued for a different purpose")

    if payload.get("s") != subject:
        raise ResourceTokenError("Token was issued for a different resource")

    if int(payload.get("e", 0)) < int(time.time()):
        raise ResourceTokenError("Token has expired")

    user_id = payload.get("u")
    if not user_id:
        raise ResourceTokenError("Token carries no user")

    return user_id
