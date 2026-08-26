#!/usr/bin/env python
"""Record owners for generated images that predate ownership recording.

The backend records who owns a generated image the first time its blob name
appears in that user's own agent output. Images generated before that existed
have no record, so the image proxy can only establish that the requester is
*some* authenticated user — not that the image is theirs.

Every stored agent message carries the same three things the live path uses:
its text, the user it belongs to, and its plan. So the owner of an older image
is recoverable from the message that delivered it, and this script replays
exactly that derivation over the messages already in the database.

It calls ``record_image_ownership`` — the same function the live path calls —
rather than reimplementing the rule. That matters: a second implementation
could drift from the first and start assigning ownership differently.

Safe to re-run. ``record_image_ownership`` is first-writer-wins, so an existing
record is never overwritten and a blob echoed by another user cannot be
reassigned. Nothing is deleted and no message is modified.

Usage, from the repository root:

    PYTHONPATH=src:src/backend python scripts/backfill_image_ownership.py --dry-run
    PYTHONPATH=src:src/backend python scripts/backfill_image_ownership.py

Requires the same Cosmos configuration and ``az login`` as running the backend.
Once it reports no images left without an owner, set
``IMAGE_REQUIRE_OWNERSHIP_RECORD=true`` so that an image with no record is
denied rather than allowed on its token. See docs/image_ownership_backfill.md.
"""

import argparse
import asyncio
import logging
import sys

from common.models.messages import AgentMessageData, DataType
from common.utils.image_assets import extract_blob_names, record_image_ownership

logger = logging.getLogger("backfill_image_ownership")

# DatabaseFactory is imported inside _open_store rather than here. Importing it
# builds the application config singleton, which raises when the backend's
# environment is not set — and that would make even `--help` fail with a
# traceback about an unrelated OpenAI variable.


async def _load_messages(memory_store):
    """Return every stored agent message, across all users.

    This is the one read here that deliberately crosses users. Every accessor
    on the database client — ``get_data_by_type``, ``get_all_items`` — pins
    ``c.user_id`` to the client's own user, which is correct for request
    handling and useless for a backfill: run that way it would match nothing
    and report a clean run over an untouched database.

    So the query is issued directly. It is safe here and only here: this is an
    offline operator script run with database credentials, and it derives each
    image's owner from the message it appears in rather than handing data
    between users.
    """
    return await memory_store.query_items(
        "SELECT * FROM c WHERE c.data_type=@data_type",
        [{"name": "@data_type", "value": DataType.m_plan_message.value}],
        AgentMessageData,
    )


def _message_text(message) -> str:
    """The text the live path scans for image references.

    ``plan_service`` scans the message content together with the streaming
    payload it was assembled from. Once stored, that payload is ``raw_data``,
    so both are scanned here to cover an image URL that only ever appeared in
    the streamed form.
    """
    content = getattr(message, "content", "") or ""
    raw = getattr(message, "raw_data", "") or ""
    return f"{content}\n{raw}"


async def _open_store():
    """Connect to the database the backend uses."""
    from common.database.database_factory import DatabaseFactory

    return await DatabaseFactory.get_database()


async def backfill(memory_store, dry_run: bool) -> int:
    """Record owners for images referenced in stored messages.

    Takes the store rather than opening one so the logic can be exercised
    without a database.

    Returns a process exit code: 0 on success, 1 if anything could not be read.
    """
    try:
        messages = await _load_messages(memory_store)
    except Exception as exc:
        logger.error("Could not read stored messages: %s", exc)
        return 1

    if not messages:
        # query_items logs and returns [] when a query fails, so "no messages"
        # and "the query failed" look identical from here. Refuse to report a
        # clean run on an empty read rather than let a failure pass as success.
        logger.error(
            "No stored agent messages were returned. Either this database holds "
            "none, or the query failed — check the log above for a CosmosDB "
            "error. Not reporting success either way."
        )
        return 1

    logger.info("Scanning %d stored agent message(s).", len(messages))

    seen: set[str] = set()
    already_owned = 0
    to_record: list[tuple[str, str, str]] = []
    skipped_no_user = 0

    for message in messages:
        user_id = getattr(message, "user_id", "") or ""
        plan_id = getattr(message, "plan_id", "") or ""
        text = _message_text(message)

        blob_names = extract_blob_names(text)
        if not blob_names:
            continue

        if not user_id:
            # Without a user there is nothing to attribute the image to. Left
            # unrecorded rather than guessed — a wrong owner is worse than none,
            # because it would deny the real owner once enforcement is on.
            skipped_no_user += len(blob_names)
            continue

        for blob_name in blob_names:
            if blob_name in seen:
                continue
            seen.add(blob_name)

            try:
                if await memory_store.get_image_asset(blob_name) is not None:
                    already_owned += 1
                    continue
            except Exception as exc:
                logger.error("Ownership lookup failed for '%s': %s", blob_name, exc)
                return 1

            to_record.append((blob_name, user_id, plan_id))

    logger.info(
        "%d image(s) referenced; %d already have an owner, %d to record, "
        "%d skipped for having no user on the message.",
        len(seen) + skipped_no_user,
        already_owned,
        len(to_record),
        skipped_no_user,
    )

    if dry_run:
        for blob_name, user_id, _ in to_record:
            logger.info("would record %s -> %s", blob_name, user_id)
        logger.info("Dry run: nothing was written.")
        return 0

    for blob_name, user_id, plan_id in to_record:
        # Pass the bare path so the same extraction the live path uses runs
        # again here, rather than trusting this script's parse of it.
        await record_image_ownership(
            memory_store, f"/api/v4/images/{blob_name}", user_id, plan_id
        )

    recorded = 0
    for blob_name, _, _ in to_record:
        if await memory_store.get_image_asset(blob_name) is not None:
            recorded += 1

    logger.info("Recorded %d of %d.", recorded, len(to_record))

    if recorded != len(to_record):
        logger.error(
            "%d image(s) were not recorded. Do not set "
            "IMAGE_REQUIRE_OWNERSHIP_RECORD until this reports a clean run.",
            len(to_record) - recorded,
        )
        return 1

    if skipped_no_user:
        logger.warning(
            "%d image reference(s) had no user on their message and remain "
            "unowned. Setting IMAGE_REQUIRE_OWNERSHIP_RECORD will deny them.",
            skipped_no_user,
        )

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be recorded without writing anything",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s  %(message)s", stream=sys.stdout
    )

    async def run() -> int:
        return await backfill(await _open_store(), args.dry_run)

    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
