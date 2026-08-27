"""Tests for ScopedCheckpointStorage.

**These tests deliberately do not touch ``agent_framework``.** An earlier revision
used the real ``WorkflowCheckpoint`` and ``InMemoryCheckpointStorage``, passed
locally, and failed in CI — CI has no ``agent_framework``, so ``conftest``
fabricates stand-in classes that are neither dataclasses nor exception types, and
ten tests died on ``replace() should be called on dataclass instances``. The
wrapper's contract is "namespace on the way in, verify ownership on the way out",
which holds against any conforming store, so it is tested against a local fake.
That makes the suite hermetic and identical in both environments.

The behaviour under test is isolation between owners sharing ONE backing store —
the situation a Cosmos-backed store creates and a per-workflow in-memory one
cannot. So the fixtures share a single fake; that sharing IS the scenario.
"""

import copy
import dataclasses
from typing import Any

import pytest

from orchestration.scoped_checkpoint_storage import (CheckpointNotFoundError,
                                                     ScopedCheckpointStorage)

WF = "magentic_workflow"


@dataclasses.dataclass
class FakeCheckpoint:
    """Stands in for WorkflowCheckpoint: the fields the wrapper touches."""

    workflow_name: str = WF
    graph_signature_hash: str = "sig"
    checkpoint_id: str = ""
    iteration_count: int = 0
    state: dict[str, Any] = dataclasses.field(default_factory=dict)


class FakeStore:
    """Minimal CheckpointStorage. Raises on a missing id, as the real one does."""

    def __init__(self) -> None:
        self._by_id: dict[str, Any] = {}
        self._next = 0

    async def save(self, checkpoint: Any) -> str:
        self._next += 1
        cid = f"cp-{self._next}"
        if dataclasses.is_dataclass(checkpoint):
            stored = dataclasses.replace(checkpoint, checkpoint_id=cid)
        else:
            # The non-dataclass conformance test passes a plain object through.
            stored = copy.copy(checkpoint)
            stored.checkpoint_id = cid
        self._by_id[cid] = stored
        return cid

    async def load(self, checkpoint_id: str) -> FakeCheckpoint:
        if checkpoint_id not in self._by_id:
            raise CheckpointNotFoundError(f"No checkpoint with ID {checkpoint_id}")
        return self._by_id[checkpoint_id]

    async def delete(self, checkpoint_id: str) -> bool:
        return self._by_id.pop(checkpoint_id, None) is not None

    async def list_checkpoints(self, *, workflow_name: str) -> list[FakeCheckpoint]:
        return [c for c in self._by_id.values() if c.workflow_name == workflow_name]

    async def list_checkpoint_ids(self, *, workflow_name: str) -> list[str]:
        return [
            c.checkpoint_id
            for c in await self.list_checkpoints(workflow_name=workflow_name)
        ]

    async def get_latest(self, *, workflow_name: str) -> FakeCheckpoint | None:
        found = await self.list_checkpoints(workflow_name=workflow_name)
        return found[-1] if found else None


@pytest.fixture
def shared():
    return FakeStore()


@pytest.fixture
def alice(shared):
    return ScopedCheckpointStorage(shared, scope="alice")


@pytest.fixture
def bob(shared):
    return ScopedCheckpointStorage(shared, scope="bob")


# --------------------------------------------------------------- construction --


def test_empty_scope_is_refused(shared):
    # Degrading silently to an unscoped store is the failure this class exists
    # to prevent, so it must not be constructible.
    with pytest.raises(ValueError, match="scope is required"):
        ScopedCheckpointStorage(shared, scope="")


def test_scope_containing_the_separator_is_refused(shared):
    # Otherwise "a::b" + "c" and "a" + "b::c" collide.
    with pytest.raises(ValueError, match="must not contain"):
        ScopedCheckpointStorage(shared, scope="al::ice")


# ------------------------------------------------------------------ isolation --


@pytest.mark.asyncio
async def test_get_latest_does_not_cross_scopes(alice, bob):
    await alice.save(FakeCheckpoint())
    # Same workflow_name, different owner — the exact collision that makes a
    # shared store unsafe without this wrapper.
    assert await bob.get_latest(workflow_name=WF) is None
    assert await alice.get_latest(workflow_name=WF) is not None


@pytest.mark.asyncio
async def test_list_checkpoints_does_not_cross_scopes(alice, bob):
    await alice.save(FakeCheckpoint())
    await alice.save(FakeCheckpoint())
    await bob.save(FakeCheckpoint())
    assert len(await alice.list_checkpoints(workflow_name=WF)) == 2
    assert len(await bob.list_checkpoints(workflow_name=WF)) == 1


@pytest.mark.asyncio
async def test_list_checkpoint_ids_does_not_cross_scopes(alice, bob):
    await alice.save(FakeCheckpoint())
    await bob.save(FakeCheckpoint())
    alice_ids = await alice.list_checkpoint_ids(workflow_name=WF)
    bob_ids = await bob.list_checkpoint_ids(workflow_name=WF)
    assert len(alice_ids) == 1 and len(bob_ids) == 1
    assert set(alice_ids).isdisjoint(bob_ids)


@pytest.mark.asyncio
async def test_load_by_id_refuses_another_scopes_checkpoint(alice, bob):
    # A checkpoint id is not a secret, so holding one must not be enough.
    cid = await alice.save(FakeCheckpoint())
    with pytest.raises(CheckpointNotFoundError):
        await bob.load(cid)
    assert (await alice.load(cid)).checkpoint_id == cid


@pytest.mark.asyncio
async def test_delete_refuses_another_scopes_checkpoint(alice, bob):
    cid = await alice.save(FakeCheckpoint())
    assert await bob.delete(cid) is False
    # And it really is still there, rather than reported as refused and removed.
    assert await alice.get_latest(workflow_name=WF) is not None
    assert await alice.delete(cid) is True
    assert await alice.get_latest(workflow_name=WF) is None


@pytest.mark.asyncio
async def test_cross_scope_and_missing_are_indistinguishable(alice, bob):
    """Both must look the same to the caller.

    A different error for "exists but is not yours" would confirm the id is real,
    which is the leak the ownership check exists to prevent.
    """
    cid = await alice.save(FakeCheckpoint())
    with pytest.raises(CheckpointNotFoundError) as cross:
        await bob.load(cid)
    with pytest.raises(CheckpointNotFoundError) as missing:
        await bob.load("no-such-checkpoint")
    assert type(cross.value) is type(missing.value)


# ------------------------------------------------------------- transparency --


@pytest.mark.asyncio
async def test_the_framework_never_sees_the_scoped_name(alice, shared):
    await alice.save(FakeCheckpoint())
    # Out through the wrapper: the name the framework wrote.
    assert (await alice.get_latest(workflow_name=WF)).workflow_name == WF
    # In the backing store: namespaced.
    assert (
        await shared.get_latest(workflow_name=f"alice::{WF}")
    ).workflow_name == f"alice::{WF}"


@pytest.mark.asyncio
async def test_save_does_not_mutate_the_callers_checkpoint(alice):
    checkpoint = FakeCheckpoint()
    await alice.save(checkpoint)
    # The framework reuses its own object after saving; renaming it in place
    # would corrupt the live workflow.
    assert checkpoint.workflow_name == WF


@pytest.mark.asyncio
async def test_payload_survives_the_round_trip(alice):
    cid = await alice.save(FakeCheckpoint(iteration_count=7, state={"k": "v"}))
    loaded = await alice.load(cid)
    assert loaded.iteration_count == 7
    assert loaded.state == {"k": "v"}
    assert loaded.graph_signature_hash == "sig"


@pytest.mark.asyncio
async def test_missing_id_raises_not_found(alice):
    with pytest.raises(CheckpointNotFoundError):
        await alice.load("no-such-checkpoint")


@pytest.mark.asyncio
async def test_delete_of_missing_id_is_false_not_an_error(alice):
    assert await alice.delete("no-such-checkpoint") is False


# ------------------------------------------------ non-dataclass conformance --


@pytest.mark.asyncio
async def test_a_store_that_returns_none_is_treated_as_not_found():
    """Some stores signal absence by returning None rather than raising.

    FakeStore and InMemoryCheckpointStorage both raise, so this branch is
    otherwise untested — and CosmosCheckpointStorage, the store this work is
    heading toward, is precisely the kind that can return None. An unhandled
    None here would surface as an AttributeError deep in the ownership check
    instead of a missing checkpoint.
    """

    class NoneReturningStore:
        async def load(self, checkpoint_id: str) -> None:
            return None

    scoped = ScopedCheckpointStorage(NoneReturningStore(), scope="dave")
    with pytest.raises(CheckpointNotFoundError):
        await scoped.load("anything")
    # delete must report False rather than raise, matching the missing-id path.
    assert await scoped.delete("anything") is False


@pytest.mark.asyncio
async def test_works_when_the_checkpoint_is_not_a_dataclass(shared):
    """The rename must not assume a dataclass.

    WorkflowCheckpoint is one today, but the wrapper is written against the
    Protocol, and CI's stand-in classes are not dataclasses — which is precisely
    how the first version of this file broke.
    """

    class PlainCheckpoint:
        def __init__(self) -> None:
            self.workflow_name = WF
            self.checkpoint_id = ""

    scoped = ScopedCheckpointStorage(shared, scope="carol")
    original = PlainCheckpoint()
    await scoped.save(original)
    assert original.workflow_name == WF  # not mutated
    assert len(await scoped.list_checkpoints(workflow_name=WF)) == 1
