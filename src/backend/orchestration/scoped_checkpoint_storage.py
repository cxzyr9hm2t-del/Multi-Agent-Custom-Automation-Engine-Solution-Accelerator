"""Owner-scoped wrapper around the framework's ``CheckpointStorage`` protocol.

**Why this exists.** ``WorkflowCheckpoint.workflow_name`` is the partition key for
every checkpoint the framework writes: ``get_latest``, ``list_checkpoints`` and
``list_checkpoint_ids`` all take it, and it is the only thing separating one
workflow's checkpoints from another's. It cannot be set on a Magentic workflow —
``MagenticBuilder.__init__`` exposes no name parameter, ``build()`` takes no
arguments, and assigning ``workflow.name`` after ``build()`` does not propagate
because ``Workflow.__init__`` constructs its ``Runner`` with ``self.name`` and the
runner copies the string. So every Magentic workflow in this process shares one
``workflow_name``.

That is harmless while the backing store is created fresh per workflow, as
``orchestration_manager`` does with ``InMemoryCheckpointStorage`` — checkpoints are
isolated by object. It stops being harmless the moment the store is shared, which
is the whole point of moving checkpoints to Cosmos: every user's plans would land
under the same key and ``get_latest`` would return whoever wrote last. Restoring
another user's workflow is a worse version of the approval-ownership hole already
fixed in ``connection_config`` — an identifier assumed to be scoped that is not.

The fix belongs here rather than upstream. ``CheckpointStorage`` is a Protocol, so
the owner can be folded into the key on the way in and stripped on the way out,
and the framework never has to know. Wrapping the in-memory store today costs
nothing and means whoever swaps the inner store inherits the isolation by default
instead of having to remember it.

``load`` and ``delete`` address a checkpoint by id alone, with no name to scope,
so they verify the record actually belongs to this scope before returning or
removing it. A checkpoint id is not a secret.

See ``docs/reports/2026-08-17-c3-spike-workflow-durability.md`` §4b.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import TYPE_CHECKING, Any

from agent_framework import WorkflowCheckpointException

if TYPE_CHECKING:  # pragma: no cover - typing only
    from agent_framework import WorkflowCheckpoint

logger = logging.getLogger(__name__)

# Separator between the scope and the framework's own workflow name. "::" is not
# produced by the framework's default names, so a round trip is unambiguous.
_SEP = "::"


class ScopedCheckpointStorage:
    """Namespace every checkpoint by owner. Satisfies ``CheckpointStorage``.

    Structural typing — the protocol is not subclassed, so this stays valid if
    the framework adds methods it can default.
    """

    def __init__(self, inner: Any, scope: str) -> None:
        if not scope:
            # Silently degrading to an unscoped store is how cross-tenant reads
            # happen, so refuse instead.
            raise ValueError("scope is required; an unscoped checkpoint store is unsafe")
        if _SEP in scope:
            raise ValueError(f"scope must not contain {_SEP!r}: {scope!r}")
        self._inner = inner
        self._scope = scope
        self._prefix = f"{scope}{_SEP}"

    # -------------------------------------------------------------- helpers --

    def _scoped(self, workflow_name: str) -> str:
        return f"{self._prefix}{workflow_name}"

    def _owned(self, checkpoint: WorkflowCheckpoint) -> bool:
        return str(checkpoint.workflow_name).startswith(self._prefix)

    def _unscope(self, checkpoint: WorkflowCheckpoint) -> WorkflowCheckpoint:
        """Hand back the name the framework wrote, not our storage key."""
        name = str(checkpoint.workflow_name)
        if not name.startswith(self._prefix):
            return checkpoint
        return dataclasses.replace(
            checkpoint, workflow_name=name[len(self._prefix):]
        )

    # -------------------------------------------------- CheckpointStorage --

    async def save(self, checkpoint: WorkflowCheckpoint) -> str:
        return await self._inner.save(
            dataclasses.replace(
                checkpoint, workflow_name=self._scoped(str(checkpoint.workflow_name))
            )
        )

    async def load(self, checkpoint_id: str) -> WorkflowCheckpoint:
        checkpoint = await self._inner.load(checkpoint_id)
        if checkpoint is None:
            raise WorkflowCheckpointException(
                f"checkpoint {checkpoint_id!r} not found"
            )
        if not self._owned(checkpoint):
            # Deliberately indistinguishable from "not found" — telling the
            # caller it exists but belongs to someone else is itself a leak.
            logger.warning(
                "Refused cross-scope checkpoint load: id=%s requested by scope=%s",
                checkpoint_id,
                self._scope,
            )
            raise WorkflowCheckpointException(
                f"checkpoint {checkpoint_id!r} not found"
            )
        return self._unscope(checkpoint)

    async def delete(self, checkpoint_id: str) -> bool:
        try:
            checkpoint = await self._inner.load(checkpoint_id)
        except Exception:
            return False
        if checkpoint is None or not self._owned(checkpoint):
            return False
        return await self._inner.delete(checkpoint_id)

    async def get_latest(self, *, workflow_name: str) -> WorkflowCheckpoint | None:
        checkpoint = await self._inner.get_latest(
            workflow_name=self._scoped(workflow_name)
        )
        if checkpoint is None or not self._owned(checkpoint):
            return None
        return self._unscope(checkpoint)

    async def list_checkpoints(self, *, workflow_name: str) -> list[WorkflowCheckpoint]:
        found = await self._inner.list_checkpoints(
            workflow_name=self._scoped(workflow_name)
        )
        return [self._unscope(c) for c in found if self._owned(c)]

    async def list_checkpoint_ids(self, *, workflow_name: str) -> list[str]:
        # Derived from list_checkpoints rather than delegated, so the ownership
        # filter cannot be bypassed by an inner store that keys ids differently.
        return [
            c.checkpoint_id
            for c in await self.list_checkpoints(workflow_name=workflow_name)
        ]
