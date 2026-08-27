"""Tests for the durable pending-decision store (Track C1).

The store is off by default, so none of this code runs in a stock deployment —
which is exactly why it needs its own tests rather than riding on the existing
suite. Two properties matter most and are asserted directly: that every
operation fails soft, because a Cosmos outage must never take down the
in-memory path that is still primary; and that an answer recorded by one
replica is adopted by a waiter in another.
"""

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# The store reaches Cosmos through DatabaseFactory, which drags in the azure
# SDK. Stub the chain before importing, as the rest of this directory does.
for _name in (
    "azure",
    "azure.cosmos",
    "azure.cosmos.aio",
    "azure.cosmos.aio._database",
    "azure.identity",
    "azure.identity.aio",
    "azure.ai",
    "azure.ai.projects",
    "azure.ai.projects.aio",
):
    sys.modules.setdefault(_name, MagicMock())

import backend.orchestration.state_store as ss  # noqa: E402
from backend.common.models.messages import OrchestrationRequest  # noqa: E402


def _record(**kw):
    base = dict(
        id="approval:p1",
        session_id="p1",
        request_id="p1",
        kind="approval",
        user_id="u1",
    )
    base.update(kw)
    return OrchestrationRequest(**base)


@pytest.fixture(autouse=True)
def _pin_model():
    """Keep the real model in place for the duration of each test.

    Other modules in this suite replace common.models.messages with a Mock, and
    module-level sys.modules edits leak across the session. When that happens
    first, OrchestrationRequest inside state_store is a Mock class, every
    attribute on the "written" record is a Mock, and the assertions here pass or
    fail on collection order rather than on behaviour.
    """
    with patch.object(ss, "OrchestrationRequest", OrchestrationRequest):
        yield


@pytest.fixture
def db():
    """A stand-in database, wired in as the store's backing."""
    database = MagicMock()
    database.upsert_orchestration_request = AsyncMock()
    database.get_orchestration_request = AsyncMock(return_value=None)
    database.delete_orchestration_request = AsyncMock()
    with patch.object(
        ss.OrchestrationStateStore, "_database", AsyncMock(return_value=database)
    ):
        yield database


@pytest.fixture
def enabled():
    with patch.object(ss.config, "ORCHESTRATION_STATE_STORE", "cosmos"):
        yield


class TestEnablement:
    def test_off_by_default(self):
        with patch.object(ss.config, "ORCHESTRATION_STATE_STORE", "memory"):
            assert ss.is_enabled() is False

    def test_absent_setting_is_off(self):
        with patch.object(ss.config, "ORCHESTRATION_STATE_STORE", ""):
            assert ss.is_enabled() is False

    def test_a_mock_config_value_does_not_switch_it_on(self):
        """config is replaced by a Mock in parts of the suite; a stand-in
        object is truthy and must not be read as opting in."""
        with patch.object(ss.config, "ORCHESTRATION_STATE_STORE", MagicMock()):
            assert ss.is_enabled() is False

    def test_cosmos_enables_it_case_insensitively(self):
        for value in ("cosmos", "Cosmos", "  COSMOS "):
            with patch.object(ss.config, "ORCHESTRATION_STATE_STORE", value):
                assert ss.is_enabled() is True

    @pytest.mark.asyncio
    async def test_disabled_touches_no_database(self, db):
        with patch.object(ss.config, "ORCHESTRATION_STATE_STORE", "memory"):
            await ss.state_store.record_pending("approval", "p1", "u1", 60)
            await ss.state_store.record_result("approval", "p1", "u1", approved=True)
            await ss.state_store.clear("approval", "p1", "u1")
            assert await ss.state_store.read("approval", "p1", "u1") is None
        db.upsert_orchestration_request.assert_not_called()
        db.get_orchestration_request.assert_not_called()


class TestDocumentId:
    def test_kinds_do_not_share_an_id_space(self):
        """A plan id and a request id could otherwise collide, and one
        clarification would release someone's approval."""
        assert ss._document_id("approval", "x") != ss._document_id(
            "clarification", "x"
        )


class TestRecording:
    @pytest.mark.asyncio
    async def test_pending_is_written_with_a_wall_clock_deadline(self, db, enabled):
        await ss.state_store.record_pending("approval", "p1", "u1", 60)
        written = db.upsert_orchestration_request.call_args.args[0]
        assert written.status == "input_required"
        assert written.user_id == "u1"
        assert written.session_id == "p1"  # partition key = point read
        # Wall clock, not monotonic: another replica cannot interpret this
        # process's monotonic origin.
        from datetime import datetime

        assert datetime.fromisoformat(written.expires_at)

    @pytest.mark.asyncio
    async def test_result_completes_the_existing_record(self, db, enabled):
        db.get_orchestration_request.return_value = _record()
        await ss.state_store.record_result("approval", "p1", "u1", approved=True)
        written = db.upsert_orchestration_request.call_args.args[0]
        assert written.status == "completed"
        assert written.approved is True

    @pytest.mark.asyncio
    async def test_result_with_no_prior_record_still_records(self, db, enabled):
        """Losing the answer because registration was missed would be worse."""
        db.get_orchestration_request.return_value = None
        await ss.state_store.record_result(
            "clarification", "r1", "u1", answer="Tuesday"
        )
        written = db.upsert_orchestration_request.call_args.args[0]
        assert written.status == "completed"
        assert written.answer == "Tuesday"


class TestReading:
    @pytest.mark.asyncio
    async def test_absent_record_is_none(self, db, enabled):
        assert await ss.state_store.read("approval", "p1", "u1") is None

    @pytest.mark.asyncio
    async def test_live_pending_record_is_returned(self, db, enabled):
        from datetime import datetime, timedelta, timezone

        future = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        db.get_orchestration_request.return_value = _record(expires_at=future)
        assert (await ss.state_store.read("approval", "p1", "u1")) is not None

    @pytest.mark.asyncio
    async def test_expired_pending_record_is_withheld(self, db, enabled):
        from datetime import datetime, timedelta, timezone

        past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        db.get_orchestration_request.return_value = _record(expires_at=past)
        assert await ss.state_store.read("approval", "p1", "u1") is None

    @pytest.mark.asyncio
    async def test_an_answer_survives_its_deadline(self, db, enabled):
        """The human did reply; the waiter's own timeout decides if it matters."""
        from datetime import datetime, timedelta, timezone

        past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        db.get_orchestration_request.return_value = _record(
            expires_at=past, status="completed", approved=True
        )
        record = await ss.state_store.read("approval", "p1", "u1")
        assert record is not None and record.approved is True

    @pytest.mark.asyncio
    async def test_an_unparseable_deadline_is_treated_as_live(self, db, enabled):
        """Fail towards keeping a real request answerable, not dropping it."""
        db.get_orchestration_request.return_value = _record(expires_at="not-a-date")
        assert (await ss.state_store.read("approval", "p1", "u1")) is not None


class TestFailsSoft:
    """A Cosmos outage must degrade to the previous behaviour, not an error."""

    @pytest.mark.asyncio
    async def test_write_failure_is_swallowed(self, db, enabled):
        db.upsert_orchestration_request.side_effect = RuntimeError("cosmos down")
        await ss.state_store.record_pending("approval", "p1", "u1", 60)
        await ss.state_store.record_result("approval", "p1", "u1", approved=True)

    @pytest.mark.asyncio
    async def test_read_failure_returns_none(self, db, enabled):
        db.get_orchestration_request.side_effect = RuntimeError("cosmos down")
        assert await ss.state_store.read("approval", "p1", "u1") is None

    @pytest.mark.asyncio
    async def test_clear_failure_is_swallowed(self, db, enabled):
        db.delete_orchestration_request.side_effect = RuntimeError("gone")
        await ss.state_store.clear("approval", "p1", "u1")

    @pytest.mark.asyncio
    async def test_an_unreachable_database_is_swallowed(self, enabled):
        with patch.object(
            ss.OrchestrationStateStore,
            "_database",
            AsyncMock(side_effect=RuntimeError("no connection")),
        ):
            await ss.state_store.record_pending("approval", "p1", "u1", 60)
            assert await ss.state_store.read("approval", "p1", "u1") is None


class TestCosmosMethods:
    """The point-read id encodes its own partition key."""

    @pytest.mark.asyncio
    async def test_malformed_id_returns_none_rather_than_raising(self):
        from backend.common.database.cosmosdb import CosmosDBClient

        client = CosmosDBClient.__new__(CosmosDBClient)
        client.get_item_by_id = AsyncMock()
        result = await CosmosDBClient.get_orchestration_request(client, "no-colon")
        assert result is None
        client.get_item_by_id.assert_not_called()

    @pytest.mark.asyncio
    async def test_partition_key_is_taken_from_the_id(self):
        from backend.common.database.cosmosdb import CosmosDBClient

        client = CosmosDBClient.__new__(CosmosDBClient)
        client.get_item_by_id = AsyncMock(return_value=None)
        await CosmosDBClient.get_orchestration_request(client, "approval:plan-9")
        args = client.get_item_by_id.call_args.args
        assert args[0] == "approval:plan-9"
        assert args[1] == "plan-9"


class TestBaseDefaults:
    """Optional capability: implementations that lack it say so clearly."""

    @pytest.mark.asyncio
    async def test_unsupported_backends_raise_not_implemented(self):
        from backend.common.database.database_base import DatabaseBase

        stub = SimpleNamespace()
        for name in (
            "upsert_orchestration_request",
            "get_orchestration_request",
        ):
            with pytest.raises(NotImplementedError):
                method = getattr(DatabaseBase, name)
                await method(stub, "x")
