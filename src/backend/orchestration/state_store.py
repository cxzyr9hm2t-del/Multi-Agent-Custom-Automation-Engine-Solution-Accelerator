"""Durable store for pending human decisions — approvals and clarifications.

Track C1 of docs/reports/2026-08-16-mrtr-migration-path.md.

**What this is for, precisely.** Approval and clarification state lives in
process memory, which is one of three reasons the backend is correct only at a
single replica. This moves the *serialisable* part of that state to Cosmos so an
answer can be recorded by whichever replica receives the HTTP request, and read
by the replica actually running the orchestration.

**What it does not fix.** The other two reasons are untouched: the live Magentic
workflow object and the ``asyncio.Task`` running it cannot be serialised, and the
WebSocket registry is inherently per-process. M3 stays open, and the
``maxReplicas: 1`` pin stays, until those are addressed too. Do not read this
module as making the backend horizontally scalable.

**Off by default.** ``ORCHESTRATION_STATE_STORE`` selects the backing:

    ``memory`` (default) — no Cosmos traffic, behaviour identical to before
    ``cosmos``           — write-through to Cosmos, and waiters poll it

Defaulting off is deliberate. This is the approval gate: a change here can hang
a plan or, worse, release someone else's. An unconfigured deployment should be
unchanged, and the store should be turned on knowingly.

Every operation fails soft. A Cosmos error must never take down the in-memory
path that is still the primary: the worst outcome of an unreachable store is the
behaviour we already had.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from common.config.app_config import config
from common.database.database_factory import DatabaseFactory
from common.models.messages import OrchestrationRequest

logger = logging.getLogger(__name__)

KIND_APPROVAL = "approval"
KIND_CLARIFICATION = "clarification"


def _document_id(kind: str, request_id: str) -> str:
    """Namespace the id by kind.

    An approval is keyed by plan_id and a clarification by its own request_id.
    Nothing guarantees those two id spaces never collide, and a collision would
    let one release the other.
    """
    return f"{kind}:{request_id}"


def is_enabled() -> bool:
    """True when the durable store is switched on."""
    configured = getattr(config, "ORCHESTRATION_STATE_STORE", "") or "memory"
    return isinstance(configured, str) and configured.strip().lower() == "cosmos"


class OrchestrationStateStore:
    """Async facade over the Cosmos-backed pending-decision records."""

    async def _database(self, user_id: str):
        return await DatabaseFactory.get_database(user_id=user_id)

    async def record_pending(
        self,
        kind: str,
        request_id: str,
        user_id: str,
        ttl_seconds: float,
    ) -> None:
        """Persist a newly registered request. Best effort."""
        if not is_enabled():
            return

        try:
            expires_at = (
                datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
            ).isoformat()
            database = await self._database(user_id)
            await database.upsert_orchestration_request(
                OrchestrationRequest(
                    id=_document_id(kind, request_id),
                    session_id=request_id,
                    request_id=request_id,
                    kind=kind,
                    user_id=user_id,
                    status="input_required",
                    expires_at=expires_at,
                )
            )
        except Exception as exc:
            logger.warning(
                "Could not persist pending %s '%s': %s", kind, request_id, exc
            )

    async def record_result(
        self,
        kind: str,
        request_id: str,
        user_id: str,
        approved: Optional[bool] = None,
        answer: Optional[str] = None,
    ) -> None:
        """Persist the answer. Best effort."""
        if not is_enabled():
            return

        try:
            database = await self._database(user_id)
            existing = await database.get_orchestration_request(
                _document_id(kind, request_id)
            )
            if existing is None:
                logger.debug(
                    "No stored %s '%s' to complete; recording it now.", kind, request_id
                )
                existing = OrchestrationRequest(
                    id=_document_id(kind, request_id),
                    session_id=request_id,
                    request_id=request_id,
                    kind=kind,
                    user_id=user_id,
                )
            existing.status = "completed"
            existing.approved = approved
            existing.answer = answer
            await database.upsert_orchestration_request(existing)
        except Exception as exc:
            logger.warning(
                "Could not persist result for %s '%s': %s", kind, request_id, exc
            )

    async def read(
        self, kind: str, request_id: str, user_id: str
    ) -> Optional[OrchestrationRequest]:
        """Return the stored request, or None if absent, expired or unreadable.

        Expiry is evaluated on read rather than by a sweeper: there is no
        background job here, and a record nobody reads costs nothing but a row.
        """
        if not is_enabled():
            return None

        try:
            database = await self._database(user_id)
            record = await database.get_orchestration_request(
                _document_id(kind, request_id)
            )
        except Exception as exc:
            logger.warning("Could not read %s '%s': %s", kind, request_id, exc)
            return None

        if record is None:
            return None

        # An answered request is returned even past its deadline: the human did
        # reply, and the waiter's own timeout decides whether it still matters.
        if record.status == "completed":
            return record

        if record.expires_at:
            try:
                if datetime.fromisoformat(record.expires_at) <= datetime.now(
                    timezone.utc
                ):
                    return None
            except ValueError:
                logger.warning(
                    "Unparseable expires_at on %s '%s'; treating as live.",
                    kind, request_id,
                )

        return record

    async def clear(self, kind: str, request_id: str, user_id: str) -> None:
        """Drop a request once it is finished with. Best effort."""
        if not is_enabled():
            return

        try:
            database = await self._database(user_id)
            await database.delete_orchestration_request(
                _document_id(kind, request_id), request_id
            )
        except Exception as exc:
            logger.debug("Could not clear %s '%s': %s", kind, request_id, exc)


state_store = OrchestrationStateStore()
