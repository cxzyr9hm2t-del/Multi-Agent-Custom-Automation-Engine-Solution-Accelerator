"""Tests for ScopedCheckpointStorage.

The behaviour under test is isolation between owners sharing one backing store,
which is the situation a Cosmos-backed checkpoint store creates and the in-memory
one does not. So these tests deliberately share a single InMemoryCheckpointStorage
between two scopes — that shared instance IS the scenario.
"""

import pytest
from agent_framework import (InMemoryCheckpointStorage, WorkflowCheckpoint,
                             WorkflowCheckpointException)

from orchestration.scoped_checkpoint_storage import ScopedCheckpointStorage

WF = "magentic_workflow"


def _checkpoint(**kw) -> WorkflowCheckpoint:
    kw.setdefault("workflow_name", WF)
    kw.setdefault("graph_signature_hash", "sig")
    return WorkflowCheckpoint(**kw)


@pytest.fixture
def shared():
    return InMemoryCheckpointStorage()


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
    await alice.save(_checkpoint())
    # Same workflow_name, different owner — the exact collision that makes a
    # shared store unsafe without this wrapper.
    assert await bob.get_latest(workflow_name=WF) is None
    assert await alice.get_latest(workflow_name=WF) is not None


@pytest.mark.asyncio
async def test_list_checkpoints_does_not_cross_scopes(alice, bob):
    await alice.save(_checkpoint())
    await alice.save(_checkpoint())
    await bob.save(_checkpoint())
    assert len(await alice.list_checkpoints(workflow_name=WF)) == 2
    assert len(await bob.list_checkpoints(workflow_name=WF)) == 1


@pytest.mark.asyncio
async def test_list_checkpoint_ids_does_not_cross_scopes(alice, bob):
    await alice.save(_checkpoint())
    await bob.save(_checkpoint())
    alice_ids = await alice.list_checkpoint_ids(workflow_name=WF)
    bob_ids = await bob.list_checkpoint_ids(workflow_name=WF)
    assert len(alice_ids) == 1 and len(bob_ids) == 1
    assert set(alice_ids).isdisjoint(bob_ids)


@pytest.mark.asyncio
async def test_load_by_id_refuses_another_scopes_checkpoint(alice, bob):
    # A checkpoint id is not a secret, so holding one must not be enough.
    cid = await alice.save(_checkpoint())
    with pytest.raises(WorkflowCheckpointException, match="not found"):
        await bob.load(cid)
    assert (await alice.load(cid)).checkpoint_id == cid


@pytest.mark.asyncio
async def test_delete_refuses_another_scopes_checkpoint(alice, bob, shared):
    cid = await alice.save(_checkpoint())
    assert await bob.delete(cid) is False
    # And it really is still there, rather than reported as refused and removed.
    assert await alice.get_latest(workflow_name=WF) is not None
    assert await alice.delete(cid) is True
    assert await alice.get_latest(workflow_name=WF) is None


# ------------------------------------------------------------- transparency --


@pytest.mark.asyncio
async def test_the_framework_never_sees_the_scoped_name(alice, shared):
    await alice.save(_checkpoint())
    # Out through the wrapper: the name the framework wrote.
    assert (await alice.get_latest(workflow_name=WF)).workflow_name == WF
    # In the backing store: namespaced.
    assert (await shared.get_latest(workflow_name=f"alice::{WF}")).workflow_name == (
        f"alice::{WF}"
    )


@pytest.mark.asyncio
async def test_save_does_not_mutate_the_callers_checkpoint(alice):
    checkpoint = _checkpoint()
    await alice.save(checkpoint)
    # The framework reuses its own object after saving; renaming it in place
    # would corrupt the live workflow.
    assert checkpoint.workflow_name == WF


@pytest.mark.asyncio
async def test_payload_survives_the_round_trip(alice):
    cid = await alice.save(_checkpoint(iteration_count=7, state={"k": "v"}))
    loaded = await alice.load(cid)
    assert loaded.iteration_count == 7
    assert loaded.state == {"k": "v"}
    assert loaded.graph_signature_hash == "sig"


@pytest.mark.asyncio
async def test_missing_id_raises_the_frameworks_exception(alice):
    # InMemoryCheckpointStorage raises rather than returning None ("No checkpoint
    # found with ID ..."), so the wrapper's own None branch is unreachable here.
    # It is kept for implementations that do return None — Cosmos among them.
    # Asserting only the type, since the message is the framework's to word.
    with pytest.raises(WorkflowCheckpointException):
        await alice.load("no-such-checkpoint")


@pytest.mark.asyncio
async def test_cross_scope_and_missing_are_indistinguishable(alice, bob):
    """Both must look the same to the caller.

    A different error for "exists but is not yours" would confirm the id is real,
    which is the leak the ownership check exists to prevent.
    """
    cid = await alice.save(_checkpoint())
    with pytest.raises(WorkflowCheckpointException) as cross:
        await bob.load(cid)
    with pytest.raises(WorkflowCheckpointException) as missing:
        await bob.load("no-such-checkpoint")
    assert type(cross.value) is type(missing.value)


@pytest.mark.asyncio
async def test_delete_of_missing_id_is_false_not_an_error(alice):
    assert await alice.delete("no-such-checkpoint") is False
