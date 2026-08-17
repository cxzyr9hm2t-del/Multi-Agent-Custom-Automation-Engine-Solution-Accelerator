"""Unit tests for backend.orchestration.connection_config.

Covers OrchestrationConfig (approval/clarification event helpers),
ConnectionConfig (WebSocket registry + status broadcasting), and
TeamConfig. WebSockets are represented by AsyncMock/MagicMock.
"""

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _import_connection_config():
    """Import backend.orchestration.connection_config with the REAL flat
    ``models.*`` / ``common.models.*`` packages, undoing any bare-Mock or
    empty-ModuleType pollution installed by earlier test modules in the same
    single-process collection run, then restore sys.modules exactly.
    """
    snapshot = dict(sys.modules)
    force_real = [
        "common",
        "common.models",
        "common.models.messages",
        "models",
        "models.messages",
        "models.plan_models",
        "backend.orchestration.connection_config",
    ]
    try:
        for name in force_real:
            sys.modules.pop(name, None)
        import backend.orchestration.connection_config as cc  # noqa: WPS433
        return cc
    finally:
        cc_mod = sys.modules.get("backend.orchestration.connection_config")
        for key in list(sys.modules):
            if key not in snapshot and not key.startswith("backend"):
                sys.modules.pop(key, None)
        sys.modules.update(snapshot)
        if cc_mod is not None:
            sys.modules["backend.orchestration.connection_config"] = cc_mod


_cc = _import_connection_config()
ConnectionConfig = _cc.ConnectionConfig
OrchestrationConfig = _cc.OrchestrationConfig
TeamConfig = _cc.TeamConfig
connection_config = _cc.connection_config
orchestration_config = _cc.orchestration_config
team_config = _cc.team_config


class TestAwaitDecisionAcrossReplicas:
    """The reason Track C1 exists.

    An asyncio.Event only fires in the process that set it. With the durable
    store on, the answer may be recorded by a different replica, and the waiter
    learns of it only by asking. With the store off, behaviour is unchanged.
    """

    @pytest.mark.asyncio
    async def test_with_the_store_off_it_waits_on_the_event_alone(self):
        cfg = OrchestrationConfig()
        cfg.set_approval_pending("p1", user_id="u1")

        polled = []

        async def _never():
            polled.append(1)
            return False

        with patch.object(_cc.state_store, "is_enabled", return_value=False):
            event = cfg._approval_events["p1"]
            event.set()
            await cfg._await_decision(event, timeout=1, poll=_never)

        # Not consulted at all — no Cosmos traffic in the default configuration.
        assert polled == []

    @pytest.mark.asyncio
    async def test_the_local_event_still_wins_immediately(self):
        cfg = OrchestrationConfig()
        cfg.set_approval_pending("p1", user_id="u1")

        async def _never():
            return False

        with patch.object(_cc.state_store, "is_enabled", return_value=True):
            event = cfg._approval_events["p1"]
            event.set()
            await cfg._await_decision(event, timeout=1, poll=_never)

    @pytest.mark.asyncio
    async def test_an_answer_from_another_replica_is_adopted(self):
        cfg = OrchestrationConfig()
        cfg.set_approval_pending("p1", user_id="u1")

        async def _found_on_second_look():
            return True

        with patch.object(_cc.state_store, "is_enabled", return_value=True), \
                patch.object(_cc, "STORE_POLL_INTERVAL_SECONDS", 0.01):
            # The event is never set — as it would not be, on this replica.
            await cfg._await_decision(
                cfg._approval_events["p1"], timeout=1, poll=_found_on_second_look
            )

    @pytest.mark.asyncio
    async def test_it_still_times_out_when_no_answer_ever_arrives(self):
        cfg = OrchestrationConfig()
        cfg.set_approval_pending("p1", user_id="u1")

        async def _never():
            return False

        with patch.object(_cc.state_store, "is_enabled", return_value=True), \
                patch.object(_cc, "STORE_POLL_INTERVAL_SECONDS", 0.01):
            with pytest.raises(asyncio.TimeoutError):
                await cfg._await_decision(
                    cfg._approval_events["p1"], timeout=0.05, poll=_never
                )

    @pytest.mark.asyncio
    async def test_a_stored_approval_is_copied_into_memory(self):
        cfg = OrchestrationConfig()
        cfg.set_approval_pending("p1", user_id="u1")
        record = MagicMock(status="completed", approved=True)

        with patch.object(
            _cc.state_store.state_store, "read", AsyncMock(return_value=record)
        ):
            assert await cfg._approval_from_store("p1") is True
        assert cfg.approvals["p1"] is True

    @pytest.mark.asyncio
    async def test_a_still_pending_stored_record_is_not_adopted(self):
        cfg = OrchestrationConfig()
        cfg.set_approval_pending("p1", user_id="u1")
        record = MagicMock(status="input_required", approved=None)

        with patch.object(
            _cc.state_store.state_store, "read", AsyncMock(return_value=record)
        ):
            assert await cfg._approval_from_store("p1") is False
        assert cfg.approvals["p1"] is None

    @pytest.mark.asyncio
    async def test_a_stored_clarification_is_copied_into_memory(self):
        cfg = OrchestrationConfig()
        cfg.set_clarification_pending("r1", user_id="u1")
        record = MagicMock(status="completed", answer="Tuesday")

        with patch.object(
            _cc.state_store.state_store, "read", AsyncMock(return_value=record)
        ):
            assert await cfg._clarification_from_store("r1") is True
        assert cfg.clarifications["r1"] == "Tuesday"

    @pytest.mark.asyncio
    async def test_the_owner_is_passed_through_so_the_read_is_scoped(self):
        cfg = OrchestrationConfig()
        cfg.set_approval_pending("p1", user_id="owner-1")
        read = AsyncMock(return_value=None)

        with patch.object(_cc.state_store.state_store, "read", read):
            await cfg._approval_from_store("p1")
        assert read.call_args.args[2] == "owner-1"


class TestPollClarification:
    """The non-blocking counterpart to wait_for_clarification.

    The MCP bridge polls instead of awaiting, so nothing holds an HTTP request
    open across the public ingress while a human types. That removes the
    awaited timeout too, which is why expiry has to be enforced here.
    """

    def test_unregistered_request_is_unknown(self):
        cfg = OrchestrationConfig()
        assert cfg.poll_clarification("nope") == ("unknown", None)

    def test_pending_request_is_input_required(self):
        cfg = OrchestrationConfig()
        cfg.set_clarification_pending("r1", user_id="u1")
        assert cfg.poll_clarification("r1") == ("input_required", None)

    def test_answered_request_is_completed(self):
        cfg = OrchestrationConfig()
        cfg.set_clarification_pending("r1", user_id="u1")
        cfg.set_clarification_result("r1", "42")
        assert cfg.poll_clarification("r1") == ("completed", "42")

    def test_an_answer_survives_being_read_twice(self):
        """A poll whose response is lost in transit must be retryable."""
        cfg = OrchestrationConfig()
        cfg.set_clarification_pending("r1", user_id="u1")
        cfg.set_clarification_result("r1", "42")
        assert cfg.poll_clarification("r1") == ("completed", "42")
        assert cfg.poll_clarification("r1") == ("completed", "42")

    def test_past_its_deadline_it_expires_and_is_dropped(self):
        cfg = OrchestrationConfig()
        cfg.set_clarification_pending("r1", user_id="u1", ttl_seconds=0)
        assert cfg.poll_clarification("r1") == ("expired", None)
        # Dropped, not merely reported — otherwise the dicts grow without bound
        # now that no awaited timeout cleans them up.
        assert cfg.poll_clarification("r1") == ("unknown", None)
        assert cfg.clarification_owner("r1") is None
        assert "r1" not in cfg._clarification_deadlines

    def test_an_answer_arriving_late_still_wins_over_expiry(self):
        """Answered is checked before the deadline: the user did reply."""
        cfg = OrchestrationConfig()
        cfg.set_clarification_pending("r1", user_id="u1", ttl_seconds=0)
        cfg.set_clarification_result("r1", "42")
        assert cfg.poll_clarification("r1") == ("completed", "42")

    def test_ttl_defaults_to_the_configured_timeout(self):
        cfg = OrchestrationConfig()
        cfg.set_clarification_pending("r1", user_id="u1")
        assert cfg.poll_clarification("r1")[0] == "input_required"

    def test_cleanup_drops_the_deadline_too(self):
        cfg = OrchestrationConfig()
        cfg.set_clarification_pending("r1", user_id="u1")
        cfg.cleanup_clarification("r1")
        assert "r1" not in cfg._clarification_deadlines


# ----------------------------------------------------------------------- #
# OrchestrationConfig
# ----------------------------------------------------------------------- #
class TestOrchestrationApproval:
    def test_get_current_orchestration(self):
        cfg = OrchestrationConfig()
        cfg.orchestrations["u1"] = "wf"
        assert cfg.get_current_orchestration("u1") == "wf"
        assert cfg.get_current_orchestration("missing") is None

    def test_set_approval_pending_creates_and_resets(self):
        cfg = OrchestrationConfig()
        cfg.set_approval_pending("p1")
        assert cfg.approvals["p1"] is None
        ev = cfg._approval_events["p1"]
        ev.set()
        cfg.set_approval_pending("p1")  # existing -> clear
        assert not cfg._approval_events["p1"].is_set()

    def test_approval_records_and_clears_its_owner(self):
        """The owner is what makes an approval answerable by one user only."""
        cfg = OrchestrationConfig()
        cfg.set_approval_pending("p1", user_id="alice")
        assert cfg.approval_owner("p1") == "alice"

        cfg.cleanup_approval("p1")
        assert cfg.approval_owner("p1") is None

    def test_approval_owner_is_none_when_not_supplied(self):
        """No owner recorded means unverifiable; callers must treat that as a denial."""
        cfg = OrchestrationConfig()
        cfg.set_approval_pending("p1")
        assert cfg.approval_owner("p1") is None

    def test_clarification_records_and_clears_its_owner(self):
        cfg = OrchestrationConfig()
        cfg.set_clarification_pending("r1", user_id="alice")
        assert cfg.clarification_owner("r1") == "alice"

        cfg.cleanup_clarification("r1")
        assert cfg.clarification_owner("r1") is None

    def test_set_approval_result_triggers_event(self):
        cfg = OrchestrationConfig()
        cfg.set_approval_pending("p1")
        cfg.set_approval_result("p1", True)
        assert cfg.approvals["p1"] is True
        assert cfg._approval_events["p1"].is_set()

    @pytest.mark.asyncio
    async def test_wait_for_approval_already_decided(self):
        cfg = OrchestrationConfig()
        cfg.approvals["p1"] = True
        assert await cfg.wait_for_approval("p1") is True

    @pytest.mark.asyncio
    async def test_wait_for_approval_missing_raises_keyerror(self):
        cfg = OrchestrationConfig()
        with pytest.raises(KeyError):
            await cfg.wait_for_approval("nope")

    @pytest.mark.asyncio
    async def test_wait_for_approval_waits_then_returns(self):
        cfg = OrchestrationConfig()
        cfg.set_approval_pending("p1")

        async def approve():
            await asyncio.sleep(0.01)
            cfg.set_approval_result("p1", True)

        task = asyncio.create_task(approve())
        result = await cfg.wait_for_approval("p1", timeout=1.0)
        await task
        assert result is True

    @pytest.mark.asyncio
    async def test_wait_for_approval_timeout(self):
        cfg = OrchestrationConfig()
        cfg.set_approval_pending("p1")
        with pytest.raises(asyncio.TimeoutError):
            await cfg.wait_for_approval("p1", timeout=0.01)
        assert "p1" not in cfg.approvals  # cleaned up

    def test_cleanup_approval(self):
        cfg = OrchestrationConfig()
        cfg.set_approval_pending("p1")
        cfg.cleanup_approval("p1")
        assert "p1" not in cfg.approvals
        assert "p1" not in cfg._approval_events


class TestOrchestrationClarification:
    def test_set_clarification_pending_and_reset(self):
        cfg = OrchestrationConfig()
        cfg.set_clarification_pending("r1")
        assert cfg.clarifications["r1"] is None
        cfg._clarification_events["r1"].set()
        cfg.set_clarification_pending("r1")
        assert not cfg._clarification_events["r1"].is_set()

    def test_set_clarification_result(self):
        cfg = OrchestrationConfig()
        cfg.set_clarification_pending("r1")
        cfg.set_clarification_result("r1", "answer")
        assert cfg.clarifications["r1"] == "answer"
        assert cfg._clarification_events["r1"].is_set()

    @pytest.mark.asyncio
    async def test_wait_for_clarification_already_answered(self):
        cfg = OrchestrationConfig()
        cfg.clarifications["r1"] = "done"
        assert await cfg.wait_for_clarification("r1") == "done"

    @pytest.mark.asyncio
    async def test_wait_for_clarification_missing_keyerror(self):
        cfg = OrchestrationConfig()
        with pytest.raises(KeyError):
            await cfg.wait_for_clarification("nope")

    @pytest.mark.asyncio
    async def test_wait_for_clarification_waits(self):
        cfg = OrchestrationConfig()
        cfg.set_clarification_pending("r1")

        async def answer():
            await asyncio.sleep(0.01)
            cfg.set_clarification_result("r1", "hi")

        task = asyncio.create_task(answer())
        result = await cfg.wait_for_clarification("r1", timeout=1.0)
        await task
        assert result == "hi"

    @pytest.mark.asyncio
    async def test_wait_for_clarification_timeout(self):
        cfg = OrchestrationConfig()
        cfg.set_clarification_pending("r1")
        with pytest.raises(asyncio.TimeoutError):
            await cfg.wait_for_clarification("r1", timeout=0.01)
        assert "r1" not in cfg.clarifications

    def test_cleanup_clarification(self):
        cfg = OrchestrationConfig()
        cfg.set_clarification_pending("r1")
        cfg.cleanup_clarification("r1")
        assert "r1" not in cfg.clarifications
        assert "r1" not in cfg._clarification_events


# ----------------------------------------------------------------------- #
# ConnectionConfig
# ----------------------------------------------------------------------- #
class TestConnectionRegistry:
    @pytest.mark.asyncio
    async def test_add_connection_simple(self):
        cc = ConnectionConfig()
        ws = AsyncMock()
        cc.add_connection("proc1", ws)
        assert cc.get_connection("proc1") is ws

    @pytest.mark.asyncio
    async def test_add_connection_with_user(self):
        cc = ConnectionConfig()
        ws = AsyncMock()
        cc.add_connection("proc1", ws, user_id="u1")
        assert cc.user_to_process["u1"] == "proc1"

    @pytest.mark.asyncio
    async def test_add_connection_replaces_existing_process(self):
        cc = ConnectionConfig()
        old = AsyncMock()
        cc.add_connection("proc1", old)
        new = AsyncMock()
        cc.add_connection("proc1", new)  # triggers close of old via create_task
        await asyncio.sleep(0)
        assert cc.get_connection("proc1") is new

    @pytest.mark.asyncio
    async def test_add_connection_closes_old_process_for_user(self):
        cc = ConnectionConfig()
        first = AsyncMock()
        cc.add_connection("procA", first, user_id="u1")
        second = AsyncMock()
        cc.add_connection("procB", second, user_id="u1")
        await asyncio.sleep(0)
        assert cc.user_to_process["u1"] == "procB"
        assert "procA" not in cc.connections

    def test_remove_connection(self):
        cc = ConnectionConfig()
        cc.connections["proc1"] = MagicMock()
        cc.user_to_process["u1"] = "proc1"
        cc.remove_connection("proc1")
        assert "proc1" not in cc.connections
        assert "u1" not in cc.user_to_process

    @pytest.mark.asyncio
    async def test_close_connection_found(self):
        cc = ConnectionConfig()
        ws = AsyncMock()
        cc.connections["proc1"] = ws
        await cc.close_connection("proc1")
        ws.close.assert_awaited_once()
        assert "proc1" not in cc.connections

    @pytest.mark.asyncio
    async def test_close_connection_missing(self):
        cc = ConnectionConfig()
        await cc.close_connection("nope")  # warns, no error

    @pytest.mark.asyncio
    async def test_close_connection_error(self):
        cc = ConnectionConfig()
        ws = AsyncMock()
        ws.close.side_effect = RuntimeError("boom")
        cc.connections["proc1"] = ws
        await cc.close_connection("proc1")
        assert "proc1" not in cc.connections


class TestSendStatusUpdateAsync:
    @pytest.mark.asyncio
    async def test_no_user_id(self):
        cc = ConnectionConfig()
        await cc.send_status_update_async("m", user_id="")  # early return

    @pytest.mark.asyncio
    async def test_fallback_single_user_in_dev(self):
        """In dev, a message for an unknown user reaches the sole connected one.

        The MCP ask_user tool takes its user_id from the model, which sometimes
        supplies a placeholder; this recovers the question locally.
        """
        import backend.orchestration.connection_config as cc_mod

        cc = ConnectionConfig()
        ws = AsyncMock()
        cc.connections["proc1"] = ws
        cc.user_to_process["real"] = "proc1"
        with patch.object(cc_mod.config, "APP_ENV", "dev"):
            await cc.send_status_update_async({"k": "v"}, user_id="wrong")
        ws.send_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_fallback_outside_dev(self):
        """Outside dev the message is dropped rather than misdelivered.

        Falling back here would hand one user's agent output — questions, plan
        content, results — to a different user who merely happens to be the
        only one connected.
        """
        import backend.orchestration.connection_config as cc_mod

        cc = ConnectionConfig()
        ws = AsyncMock()
        cc.connections["proc1"] = ws
        cc.user_to_process["real"] = "proc1"
        with patch.object(cc_mod.config, "APP_ENV", "prod"):
            await cc.send_status_update_async({"k": "v"}, user_id="wrong")
        ws.send_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_process_multiple_users(self):
        cc = ConnectionConfig()
        cc.user_to_process["a"] = "p1"
        cc.user_to_process["b"] = "p2"
        await cc.send_status_update_async("m", user_id="wrong")  # returns, no send

    @pytest.mark.asyncio
    async def test_message_with_to_dict(self):
        cc = ConnectionConfig()
        ws = AsyncMock()
        cc.connections["p1"] = ws
        cc.user_to_process["u1"] = "p1"
        msg = MagicMock()
        msg.to_dict.return_value = {"x": 1}
        await cc.send_status_update_async(msg, user_id="u1")
        ws.send_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_message_to_dict_error_falls_back_to_str(self):
        cc = ConnectionConfig()
        ws = AsyncMock()
        cc.connections["p1"] = ws
        cc.user_to_process["u1"] = "p1"
        msg = MagicMock()
        msg.to_dict.side_effect = RuntimeError("bad")
        await cc.send_status_update_async(msg, user_id="u1")
        ws.send_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_send_error_removes_connection(self):
        cc = ConnectionConfig()
        ws = AsyncMock()
        ws.send_text.side_effect = RuntimeError("boom")
        cc.connections["p1"] = ws
        cc.user_to_process["u1"] = "p1"
        await cc.send_status_update_async("m", user_id="u1")
        assert "p1" not in cc.connections

    @pytest.mark.asyncio
    async def test_no_connection_for_process(self):
        cc = ConnectionConfig()
        cc.user_to_process["u1"] = "p1"  # mapped but no connection object
        await cc.send_status_update_async("m", user_id="u1")
        assert "u1" not in cc.user_to_process


class TestSendStatusUpdateSync:
    @pytest.mark.asyncio
    async def test_sync_send_found(self):
        cc = ConnectionConfig()
        ws = AsyncMock()
        cc.connections["p1"] = ws
        cc.send_status_update("hello", "p1")
        await asyncio.sleep(0)
        ws.send_text.assert_awaited_once_with("hello")

    def test_sync_send_not_found(self):
        cc = ConnectionConfig()
        cc.send_status_update("hello", "missing")  # warns, no error


# ----------------------------------------------------------------------- #
# TeamConfig
# ----------------------------------------------------------------------- #
class TestTeamConfig:
    def test_set_and_get(self):
        tc = TeamConfig()
        team = MagicMock()
        tc.set_current_team("u1", team)
        assert tc.get_current_team("u1") is team

    def test_get_missing(self):
        tc = TeamConfig()
        assert tc.get_current_team("nope") is None


def test_module_singletons():
    assert isinstance(orchestration_config, OrchestrationConfig)
    assert isinstance(connection_config, ConnectionConfig)
    assert isinstance(team_config, TeamConfig)
