"""Recording which user a generated image belongs to.

The MCP server uploads generated images under a ``uuid4`` blob name and returns
``/api/v4/images/<blob>``. It has no reliable way to know who asked for the
image: ``generate_marketing_image`` takes no user argument, and having the model
carry one is the mechanism that proved unreliable for ``ask_user``.

The backend does know. Every image URL reaches a user through that user's own
agent output, so ownership is recorded here the first time a blob name appears
in text belonging to them. The image proxy then has something to check a
requester against, rather than only establishing that they are *some*
authenticated user.
"""

import logging
import re
import uuid

from common.models.messages import ImageAsset

logger = logging.getLogger(__name__)

# Matches the blob name in a backend image path. Names are uuid4 + ".png"; the
# pattern is deliberately strict so arbitrary text cannot register a claim on a
# blob that does not look like one we generated.
_IMAGE_PATH_RE = re.compile(
    r"/api/v4/images/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\.png)"
)


def extract_blob_names(text: str) -> list[str]:
    """Return the generated-image blob names referenced in `text`, de-duplicated."""
    if not text:
        return []
    seen = {}
    for match in _IMAGE_PATH_RE.finditer(text):
        seen.setdefault(match.group(1).lower(), None)
    return list(seen)


async def record_image_ownership(
    memory_store, text: str, user_id: str, plan_id: str = ""
) -> None:
    """Record `user_id` as the owner of any images referenced in `text`.

    Best-effort: a failure here must not break the message it was derived from,
    so everything is caught and logged. The consequence of a miss is an image
    that falls back to token-only protection, not a broken response.

    First writer wins — an existing record is never overwritten, so a blob name
    echoed back by another user cannot reassign ownership.
    """
    if not user_id or not text:
        return

    for blob_name in extract_blob_names(text):
        try:
            existing = await memory_store.get_image_asset(blob_name)
            if existing is not None:
                continue

            await memory_store.add_image_asset(
                ImageAsset(
                    id=str(uuid.uuid4()),
                    session_id=plan_id or user_id,
                    blob_name=blob_name,
                    user_id=user_id,
                    plan_id=plan_id or "",
                )
            )
            logger.debug("Recorded image '%s' as owned by '%s'", blob_name, user_id)
        except Exception as exc:
            logger.warning(
                "Could not record ownership of image '%s': %s", blob_name, exc
            )
