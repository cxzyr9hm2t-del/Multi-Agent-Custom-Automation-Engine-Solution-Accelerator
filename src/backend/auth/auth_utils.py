import base64
import json
import logging

from common.config.app_config import config


def get_authenticated_user_details(request_headers):
    user_object = {}

    normalized_headers = {k.lower(): v for k, v in request_headers.items()}

    # A front door identifies the caller with either the claims document or the
    # scalar id header — Container Apps auth and App Service EasyAuth both emit
    # both, but accept either as evidence rather than gating on one of them.
    has_principal = (
        "x-ms-client-principal" in normalized_headers
        or "x-ms-client-principal-id" in normalized_headers
    )

    if not has_principal:
        logging.info("No user principal found in headers")
        # Outside development, an absent principal means the request did not come
        # through an authenticating front door. Fall through with no identity so
        # callers reject it, rather than substituting the sample user — whose
        # all-zeros principal would otherwise become a shared anonymous account
        # that every unauthenticated caller lands in.
        if config.APP_ENV != "dev":
            raw_user_object = {}
        else:
            from . import sample_user

            raw_user_object = sample_user.sample_user
        normalized_headers = {k.lower(): v for k, v in raw_user_object.items()}
    user_object["user_principal_id"] = _principal_id_from_headers(normalized_headers)
    user_object["user_name"] = normalized_headers.get("x-ms-client-principal-name")
    user_object["auth_provider"] = normalized_headers.get("x-ms-client-principal-idp")
    user_object["auth_token"] = normalized_headers.get("x-ms-token-aad-id-token")
    user_object["client_principal_b64"] = normalized_headers.get(
        "x-ms-client-principal"
    )
    user_object["aad_id_token"] = normalized_headers.get("x-ms-token-aad-id-token")

    return user_object


def _principal_id_from_headers(normalized_headers):
    """Resolve the caller's principal id, preferring the platform claims blob.

    ``x-ms-client-principal`` is a base64 JSON claims document that the auth
    front door (Container Apps built-in auth, or App Service EasyAuth) injects
    after validating the caller's token, stripping any copy the client sent. Its
    object-id claim is therefore the trustworthy identity once a front door is
    configured — whereas ``x-ms-client-principal-id`` is a bare scalar that
    looks identical whether the platform set it or the caller did.

    Falls back to the scalar header when no claims blob is present or it cannot
    be parsed, which keeps local development and any front door that only emits
    the scalar working.

    NOTE: neither source is trustworthy unless an auth front door is actually in
    front of this app. See docs/backend_api_authentication.md.
    """
    logger = logging.getLogger(__name__)
    client_principal_b64 = normalized_headers.get("x-ms-client-principal")

    if client_principal_b64:
        try:
            decoded = base64.b64decode(client_principal_b64).decode("utf-8")
            principal = json.loads(decoded)
        except Exception:
            # A malformed blob is not fatal — fall through to the scalar header.
            logger.debug("Could not decode x-ms-client-principal; using id header")
        else:
            claims = principal.get("claims") or []
            by_type = {
                claim.get("typ"): claim.get("val")
                for claim in claims
                if isinstance(claim, dict)
            }
            for claim_type in (
                "http://schemas.microsoft.com/identity/claims/objectidentifier",
                "oid",
                "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/nameidentifier",
                "sub",
            ):
                if by_type.get(claim_type):
                    return by_type[claim_type]

    return normalized_headers.get("x-ms-client-principal-id")


def get_tenantid(client_principal_b64):
    logger = logging.getLogger(__name__)
    tenant_id = ""
    if client_principal_b64:
        try:
            # Decode the base64 header to get the JSON string
            decoded_bytes = base64.b64decode(client_principal_b64)
            decoded_string = decoded_bytes.decode("utf-8")
            # Convert the JSON string into a Python dictionary
            user_info = json.loads(decoded_string)
            # Extract the tenant ID
            tenant_id = user_info.get("tid")  # 'tid' typically holds the tenant ID
        except Exception as ex:
            logger.exception(ex)
    return tenant_id
