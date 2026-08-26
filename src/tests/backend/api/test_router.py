# Copyright (c) Microsoft. All rights reserved.
"""Tests for api/router.py.

These tests import the *real* router module and patch its collaborators at the
module level (never via sys.modules), so they do not pollute the shared
interpreter state for other test files that import the same real modules.
"""

import os
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

# Ensure flat backend imports (models.messages etc.) inside router resolve.
_backend_path = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend")
)
if _backend_path not in sys.path:
    sys.path.insert(0, _backend_path)


def _import_router():
    """Import the real router module despite shared-process mock pollution.

    Earlier-collected tests (e.g. agents/) replace flat modules such as
    ``common.database`` with bare ``Mock()`` objects in ``sys.modules``. Those
    are not packages, so the router's flat imports would fail. We install proper
    package stubs for the flat namespaces the router walks and ``MagicMock``
    stand-ins for its heavy leaf dependencies, letting the lightweight message
    model modules import for real (so FastAPI request/response validation uses
    the genuine dataclasses/pydantic models). Afterwards ``sys.modules`` is
    restored to its exact prior state so no other test file is affected. The
    router's collaborators are patched per-test.
    """
    def _realpkg(name):
        module = ModuleType(name)
        module.__path__ = [os.path.join(_backend_path, *name.split("."))]
        sys.modules[name] = module

    packages = [
        "common", "common.models", "common.config", "common.database",
        "common.utils", "orchestration", "orchestration.helper", "services",
        "auth", "models",
    ]
    heavy_leaves = [
        "common.config.app_config", "common.database.database_factory",
        "common.utils.event_utils", "common.utils.team_utils",
        "orchestration.connection_config", "orchestration.orchestration_manager",
        "services.plan_service", "services.team_service", "auth.auth_utils",
    ]
    # Leaf modules that MUST load for real so FastAPI sees genuine model classes.
    force_real = ["common.models.messages", "models.messages", "models.plan_models"]
    snapshot = dict(sys.modules)
    try:
        for pkg in packages:
            _realpkg(pkg)
        for leaf in heavy_leaves:
            sys.modules[leaf] = MagicMock()
        for name in force_real:
            sys.modules.pop(name, None)
        import backend.api.router as router  # noqa: F401
        from fastapi import FastAPI

        # Build the app while the real message-model modules are importable, so
        # FastAPI resolves the route signatures against the genuine models.
        app = FastAPI()
        app.include_router(router.app_router)
        return router, app
    finally:
        router_mod_obj = sys.modules.get("backend.api.router")
        for key in list(sys.modules):
            if key not in snapshot and not key.startswith("backend"):
                del sys.modules[key]
        for key, value in snapshot.items():
            sys.modules[key] = value
        if router_mod_obj is not None:
            sys.modules["backend.api.router"] = router_mod_obj


router_mod, _app = _import_router()
from fastapi.testclient import TestClient  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture: TestClient with all collaborators mocked
# ---------------------------------------------------------------------------
@pytest.fixture
def rt(monkeypatch):
    """Patch every collaborator referenced from the router namespace."""
    store = MagicMock()
    store.get_plan_by_plan_id = AsyncMock(return_value=None)
    store.get_current_team = AsyncMock(return_value=None)
    store.get_team_by_id = AsyncMock(return_value=MagicMock())
    store.get_plan = AsyncMock(return_value=None)
    store.get_agent_messages = AsyncMock(return_value=[])
    store.get_all_plans_by_team_id_status = AsyncMock(return_value=[])
    store.delete_current_team = AsyncMock()
    store.add_plan = AsyncMock()

    database_factory = MagicMock()
    database_factory.get_database = AsyncMock(return_value=store)

    team_service = MagicMock()
    team_service.get_team_configuration = AsyncMock(return_value=None)
    team_service.handle_team_selection = AsyncMock(return_value=MagicMock())
    team_service.get_all_team_configurations = AsyncMock(return_value=[])
    team_service.delete_team_configuration = AsyncMock(return_value=True)
    team_service.validate_team_models = AsyncMock(return_value=(True, []))
    team_service.validate_team_search_indexes = AsyncMock(return_value=(True, []))
    team_service.validate_and_parse_team_config = AsyncMock(return_value=MagicMock())
    team_service.save_team_configuration = AsyncMock(return_value="team-123")
    team_service_cls = MagicMock(return_value=team_service)

    plan_service = MagicMock()
    plan_service.handle_plan_approval = AsyncMock(return_value=True)
    plan_service.handle_human_clarification = AsyncMock(return_value=True)
    plan_service.handle_agent_messages = AsyncMock(return_value=True)

    orchestration_manager = MagicMock()
    orchestration_manager.get_current_or_new_orchestration = AsyncMock()
    orchestration_manager.return_value.run_orchestration = AsyncMock()

    connection_config = MagicMock()
    connection_config.send_status_update_async = AsyncMock()
    connection_config.close_connection = AsyncMock()
    connection_config.add_connection = MagicMock()
    connection_config.wait_for_clarification = AsyncMock(return_value="the answer")

    orchestration_config = MagicMock()
    orchestration_config.wait_for_clarification = AsyncMock(return_value="the answer")
    orchestration_config.approvals = {}
    orchestration_config.clarifications = {}
    orchestration_config.plans = {}
    orchestration_config.active_tasks = {}
    orchestration_config.get_current_orchestration = MagicMock(return_value=None)
    orchestration_config.set_approval_result = MagicMock()
    orchestration_config.set_clarification_result = MagicMock()
    orchestration_config.set_clarification_pending = MagicMock()
    # Pending approvals and clarifications belong to the authenticated test user
    # by default; individual tests override these to exercise a foreign caller.
    orchestration_config.approval_owner = MagicMock(return_value="user-1")
    orchestration_config.clarification_owner = MagicMock(return_value="user-1")

    team_config = MagicMock()

    find_first_available_team = AsyncMock(return_value="team-abc")
    rai_success = AsyncMock(return_value=True)
    rai_validate_team_config = AsyncMock(return_value=(True, None))
    get_user = MagicMock(return_value={"user_principal_id": "user-1"})

    monkeypatch.setattr(router_mod, "get_authenticated_user_details", get_user)
    monkeypatch.setattr(router_mod, "DatabaseFactory", database_factory)
    monkeypatch.setattr(router_mod, "TeamService", team_service_cls)
    monkeypatch.setattr(router_mod, "PlanService", plan_service)
    monkeypatch.setattr(router_mod, "OrchestrationManager", orchestration_manager)
    monkeypatch.setattr(router_mod, "connection_config", connection_config)
    monkeypatch.setattr(router_mod, "orchestration_config", orchestration_config)
    monkeypatch.setattr(router_mod, "team_config", team_config)
    monkeypatch.setattr(router_mod, "track_event_if_configured", MagicMock())
    monkeypatch.setattr(
        router_mod, "find_first_available_team", find_first_available_team
    )
    monkeypatch.setattr(router_mod, "rai_success", rai_success)
    monkeypatch.setattr(router_mod, "rai_validate_team_config", rai_validate_team_config)

    app = _app
    client = TestClient(app)

    return SimpleNamespace(
        client=client,
        store=store,
        database_factory=database_factory,
        team_service=team_service,
        team_service_cls=team_service_cls,
        plan_service=plan_service,
        orchestration_manager=orchestration_manager,
        connection_config=connection_config,
        orchestration_config=orchestration_config,
        team_config=team_config,
        find_first_available_team=find_first_available_team,
        rai_success=rai_success,
        rai_validate_team_config=rai_validate_team_config,
        get_user=get_user,
    )


def _no_user(rt):
    rt.get_user.return_value = {"user_principal_id": None}


# ---------------------------------------------------------------------------
# /init_team
# ---------------------------------------------------------------------------
class TestInitTeam:
    def test_no_user(self, rt):
        _no_user(rt)
        resp = rt.client.get("/api/v4/init_team")
        assert resp.status_code == 400

    def test_no_teams_configured(self, rt):
        rt.find_first_available_team.return_value = None
        rt.store.get_current_team.return_value = None
        resp = rt.client.get("/api/v4/init_team")
        assert resp.status_code == 200
        body = resp.json()
        assert body["requires_team_upload"] is True

    def test_first_available_team_used(self, rt):
        rt.find_first_available_team.return_value = "team-abc"
        rt.store.get_current_team.return_value = None
        selected = MagicMock()
        selected.team_id = "team-abc"
        rt.team_service.handle_team_selection.return_value = selected
        team_conf = MagicMock()
        rt.team_service.get_team_configuration.return_value = team_conf
        resp = rt.client.get("/api/v4/init_team")
        assert resp.status_code == 200
        assert resp.json()["status"] == "Request started successfully"

    def test_current_team_used(self, rt):
        current = MagicMock()
        current.team_id = "team-current"
        rt.store.get_current_team.return_value = current
        rt.team_service.get_team_configuration.return_value = MagicMock()
        resp = rt.client.get("/api/v4/init_team")
        assert resp.status_code == 200
        assert resp.json()["team_id"] == "team-current"

    def test_team_configuration_missing_clears(self, rt):
        current = MagicMock()
        current.team_id = "team-current"
        rt.store.get_current_team.return_value = current
        rt.team_service.get_team_configuration.return_value = None
        resp = rt.client.get("/api/v4/init_team")
        assert resp.status_code == 200
        assert resp.json()["requires_team_upload"] is True
        rt.store.delete_current_team.assert_awaited()

    def test_exception_returns_400(self, rt):
        rt.database_factory.get_database = AsyncMock(side_effect=Exception("boom"))
        resp = rt.client.get("/api/v4/init_team")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# /process_request
# ---------------------------------------------------------------------------
class TestProcessRequest:
    def _payload(self):
        return {"session_id": "sess-1", "description": "do the thing"}

    def test_no_user(self, rt):
        _no_user(rt)
        resp = rt.client.post("/api/v4/process_request", json=self._payload())
        assert resp.status_code == 400

    def test_team_not_found(self, rt):
        """A missing team is a 404 and reaches the client as one.

        The enclosing `except Exception` used to fold this deliberate 404 into
        the generic 400 for team-retrieval errors.
        """
        rt.store.get_current_team.return_value = None
        rt.store.get_team_by_id.return_value = None
        resp = rt.client.post("/api/v4/process_request", json=self._payload())
        assert resp.status_code == 404

    def test_rai_failure(self, rt):
        rt.store.get_team_by_id.return_value = MagicMock()
        rt.rai_success.return_value = False
        resp = rt.client.post("/api/v4/process_request", json=self._payload())
        assert resp.status_code == 400

    def test_success(self, rt):
        team = MagicMock()
        rt.store.get_team_by_id.return_value = team
        current = MagicMock()
        current.team_id = "team-x"
        rt.store.get_current_team.return_value = current
        rt.rai_success.return_value = True
        resp = rt.client.post("/api/v4/process_request", json=self._payload())
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "Request started successfully"
        assert body["plan_id"]

    def test_success_generates_session_id(self, rt):
        rt.store.get_team_by_id.return_value = MagicMock()
        rt.rai_success.return_value = True
        resp = rt.client.post(
            "/api/v4/process_request", json={"session_id": "", "description": "x"}
        )
        assert resp.status_code == 200
        assert resp.json()["session_id"]


# ---------------------------------------------------------------------------
# /plan_approval
# ---------------------------------------------------------------------------
class TestPlanApproval:
    def _payload(self, **kw):
        data = {"m_plan_id": "m-1", "approved": True, "plan_id": "p-1", "feedback": "ok"}
        data.update(kw)
        return data

    def test_no_user(self, rt):
        _no_user(rt)
        resp = rt.client.post("/api/v4/plan_approval", json=self._payload())
        assert resp.status_code == 401

    def test_approved_recorded(self, rt):
        rt.orchestration_config.approvals = {"m-1": True}
        plan = MagicMock()
        plan.session_id = "sess-1"
        rt.store.get_plan_by_plan_id.return_value = plan
        resp = rt.client.post("/api/v4/plan_approval", json=self._payload())
        assert resp.status_code == 200
        assert resp.json()["status"] == "approval recorded"
        rt.orchestration_config.set_approval_result.assert_called_once()

    def test_rejected_recorded(self, rt):
        rt.orchestration_config.approvals = {"m-1": True}
        rt.store.get_plan_by_plan_id.return_value = None
        resp = rt.client.post(
            "/api/v4/plan_approval", json=self._payload(approved=False)
        )
        assert resp.status_code == 200

    def test_no_active_plan(self, rt):
        """An unknown m_plan_id is a 404 and reaches the client as one.

        The surrounding `except Exception` used to swallow this and report a
        500; deliberate status codes now re-raise ahead of the catch-all.
        """
        rt.orchestration_config.approvals = {}
        resp = rt.client.post("/api/v4/plan_approval", json=self._payload())
        assert resp.status_code == 404

    def test_approval_by_non_owner_is_forbidden(self, rt):
        """Holding a live m_plan_id is not authority to approve it.

        The approval gate is the human-in-the-loop control: approving releases
        the agent workflow to execute. A caller who is not the plan's owner must
        not be able to do that, even with a valid pending id.
        """
        rt.orchestration_config.approvals = {"m-1": True}
        rt.orchestration_config.approval_owner = MagicMock(return_value="someone-else")
        resp = rt.client.post("/api/v4/plan_approval", json=self._payload())
        assert resp.status_code == 403
        rt.orchestration_config.set_approval_result.assert_not_called()

    def test_approval_with_unrecorded_owner_is_forbidden(self, rt):
        """An approval with no recorded owner cannot be verified, so it is denied."""
        rt.orchestration_config.approvals = {"m-1": True}
        rt.orchestration_config.approval_owner = MagicMock(return_value=None)
        resp = rt.client.post("/api/v4/plan_approval", json=self._payload())
        assert resp.status_code == 403
        rt.orchestration_config.set_approval_result.assert_not_called()

    def test_plan_service_value_error(self, rt):
        rt.orchestration_config.approvals = {"m-1": True}
        rt.plan_service.handle_plan_approval = AsyncMock(side_effect=ValueError("bad"))
        resp = rt.client.post("/api/v4/plan_approval", json=self._payload())
        assert resp.status_code == 200

    def test_plan_service_generic_error(self, rt):
        rt.orchestration_config.approvals = {"m-1": True}
        rt.plan_service.handle_plan_approval = AsyncMock(side_effect=Exception("boom"))
        resp = rt.client.post("/api/v4/plan_approval", json=self._payload())
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# /clarification/ask
# ---------------------------------------------------------------------------
class TestClarificationAsk:
    def test_missing_fields(self, rt):
        resp = rt.client.post("/api/v4/clarification/ask", json={"question": ""})
        assert resp.status_code == 400

    def _token(self, user_id="user-1"):
        resource_tokens = router_mod.resource_tokens
        return resource_tokens.mint(
            resource_tokens.PURPOSE_CLARIFY, "", user_id, 60
        )

    def test_success(self, rt):
        rt.orchestration_config.wait_for_clarification.return_value = "answer!"
        resp = rt.client.post(
            "/api/v4/clarification/ask",
            json={"question": "why?", "session_token": self._token()},
        )
        assert resp.status_code == 200
        assert resp.json()["answer"] == "answer!"

    def test_the_user_comes_from_the_token_not_the_body(self, rt):
        """The delivery target is derived from the signature, not the payload.

        A caller that signs as one user and names another in the body must be
        treated as the user it can actually prove.
        """
        rt.orchestration_config.wait_for_clarification.return_value = "answer!"
        resp = rt.client.post(
            "/api/v4/clarification/ask",
            json={
                "question": "why?",
                "session_token": self._token("owner"),
                "user_id": "someone-else",
            },
        )
        assert resp.status_code == 200
        rt.orchestration_config.set_clarification_pending.assert_called_once()
        assert (
            rt.orchestration_config.set_clarification_pending.call_args.kwargs["user_id"]
            == "owner"
        )

    def test_no_token_is_refused(self, rt):
        resp = rt.client.post(
            "/api/v4/clarification/ask",
            json={"question": "why?", "user_id": "user-1"},
        )
        assert resp.status_code == 401

    def test_a_forged_token_is_refused(self, rt):
        resp = rt.client.post(
            "/api/v4/clarification/ask",
            json={"question": "why?", "session_token": "not.a.token"},
        )
        assert resp.status_code == 401

    def test_a_token_for_another_purpose_is_refused(self, rt):
        """An image token must not double as permission to interrupt a user."""
        resource_tokens = router_mod.resource_tokens
        token = resource_tokens.mint(
            resource_tokens.PURPOSE_IMAGE, "", "user-1", 60
        )
        resp = rt.client.post(
            "/api/v4/clarification/ask",
            json={"question": "why?", "session_token": token},
        )
        assert resp.status_code == 401

    def test_timeout(self, rt):
        import asyncio

        rt.orchestration_config.wait_for_clarification = AsyncMock(
            side_effect=asyncio.TimeoutError()
        )
        resp = rt.client.post(
            "/api/v4/clarification/ask",
            json={"question": "why?", "session_token": self._token()},
        )
        assert resp.status_code == 200
        assert resp.json()["answer"] == ""

    def test_generic_error(self, rt):
        rt.orchestration_config.wait_for_clarification = AsyncMock(
            side_effect=Exception("boom")
        )
        resp = rt.client.post(
            "/api/v4/clarification/ask",
            json={"question": "why?", "session_token": self._token()},
        )
        assert resp.status_code == 200
        assert resp.json()["answer"] == ""


# ---------------------------------------------------------------------------
# /user_clarification
# ---------------------------------------------------------------------------
class TestUserClarification:
    def _payload(self, **kw):
        data = {"request_id": "r-1", "answer": "my answer", "plan_id": "p-1"}
        data.update(kw)
        return data

    def test_no_user(self, rt):
        _no_user(rt)
        resp = rt.client.post("/api/v4/user_clarification", json=self._payload())
        assert resp.status_code == 401

    def test_team_not_found(self, rt):
        rt.store.get_team_by_id.return_value = None
        resp = rt.client.post("/api/v4/user_clarification", json=self._payload())
        assert resp.status_code == 404

    def test_rai_failure(self, rt):
        rt.store.get_team_by_id.return_value = MagicMock()
        rt.rai_success.return_value = False
        resp = rt.client.post("/api/v4/user_clarification", json=self._payload())
        assert resp.status_code == 400

    def test_success(self, rt):
        rt.store.get_team_by_id.return_value = MagicMock()
        rt.rai_success.return_value = True
        rt.orchestration_config.clarifications = {"r-1": True}
        resp = rt.client.post("/api/v4/user_clarification", json=self._payload())
        assert resp.status_code == 200
        assert resp.json()["status"] == "clarification recorded"

    def test_no_active_clarification(self, rt):
        rt.store.get_team_by_id.return_value = MagicMock()
        rt.rai_success.return_value = True
        rt.orchestration_config.clarifications = {}
        resp = rt.client.post("/api/v4/user_clarification", json=self._payload())
        assert resp.status_code == 404

    def test_clarification_by_non_owner_is_forbidden(self, rt):
        """Only the user a question was put to may answer it.

        The request_id travels to the browser over the WebSocket, so possessing
        one says nothing about who was asked. An answer feeds straight back into
        the agent loop.
        """
        rt.store.get_team_by_id.return_value = MagicMock()
        rt.rai_success.return_value = True
        rt.orchestration_config.clarifications = {"r-1": True}
        rt.orchestration_config.clarification_owner = MagicMock(
            return_value="someone-else"
        )
        resp = rt.client.post("/api/v4/user_clarification", json=self._payload())
        assert resp.status_code == 403
        rt.orchestration_config.set_clarification_result.assert_not_called()


# ---------------------------------------------------------------------------
# /agent_message
# ---------------------------------------------------------------------------
class TestAgentMessage:
    def _payload(self, **kw):
        data = {
            "plan_id": "p-1",
            "agent": "My Agent",
            "content": "hello",
            "agent_type": "AI_Agent",
        }
        data.update(kw)
        return data

    def test_no_user(self, rt):
        _no_user(rt)
        resp = rt.client.post("/api/v4/agent_message", json=self._payload())
        assert resp.status_code == 401

    def test_success(self, rt):
        plan = MagicMock()
        plan.session_id = "sess-1"
        rt.store.get_plan_by_plan_id.return_value = plan
        resp = rt.client.post("/api/v4/agent_message", json=self._payload())
        assert resp.status_code == 200
        assert resp.json()["status"] == "message recorded"

    def test_plan_service_error(self, rt):
        plan = MagicMock()
        plan.session_id = "sess-1"
        rt.store.get_plan_by_plan_id.return_value = plan
        rt.plan_service.handle_agent_messages = AsyncMock(side_effect=Exception("boom"))
        resp = rt.client.post("/api/v4/agent_message", json=self._payload())
        assert resp.status_code == 200

    def test_message_for_a_plan_you_do_not_own_is_rejected(self, rt):
        """Appending to another user's transcript, and closing their plan out.

        On is_final this handler sets overall_status=completed and overwrites
        streaming_message with caller-supplied content, so the write is
        authorized against the plan it targets.
        """
        rt.store.get_plan_by_plan_id.return_value = None
        resp = rt.client.post("/api/v4/agent_message", json=self._payload())
        assert resp.status_code == 404
        rt.plan_service.handle_agent_messages.assert_not_called()

    def test_message_without_plan_id_is_rejected(self, rt):
        """With no plan_id the write can be neither attributed nor authorized."""
        resp = rt.client.post("/api/v4/agent_message", json=self._payload(plan_id=""))
        assert resp.status_code == 400
        rt.plan_service.handle_agent_messages.assert_not_called()


# ---------------------------------------------------------------------------
# /socket_token and /image_token
# ---------------------------------------------------------------------------
class TestResourceTokenEndpoints:
    def test_socket_token_requires_a_user(self, rt):
        _no_user(rt)
        resp = rt.client.post("/api/v4/socket_token?plan_id=p-1")
        assert resp.status_code == 401

    def test_socket_token_is_bound_to_the_plan_and_the_caller(self, rt):
        resource_tokens = router_mod.resource_tokens

        rt.store.get_plan_by_plan_id.return_value = MagicMock()
        resp = rt.client.post("/api/v4/socket_token?plan_id=p-1")

        assert resp.status_code == 200
        token = resp.json()["token"]
        assert resource_tokens.verify(
            token, resource_tokens.PURPOSE_SOCKET, "p-1"
        ) == "user-1"

    def test_socket_token_for_a_plan_you_do_not_own_is_404(self, rt):
        """The plan lookup is user-scoped, so a foreign plan does not resolve."""
        rt.store.get_plan_by_plan_id.return_value = None
        resp = rt.client.post("/api/v4/socket_token?plan_id=someone-elses")
        assert resp.status_code == 404

    def test_image_token_requires_a_user(self, rt):
        _no_user(rt)
        resp = rt.client.post("/api/v4/image_token")
        assert resp.status_code == 401

    def test_image_token_is_issued_to_the_caller(self, rt):
        resource_tokens = router_mod.resource_tokens

        resp = rt.client.post("/api/v4/image_token")

        assert resp.status_code == 200
        token = resp.json()["token"]
        assert resource_tokens.verify(
            token, resource_tokens.PURPOSE_IMAGE, ""
        ) == "user-1"

    def test_a_socket_token_cannot_be_replayed_as_an_image_token(self, rt):
        """Purpose is part of the signed payload."""
        resource_tokens = router_mod.resource_tokens

        rt.store.get_plan_by_plan_id.return_value = MagicMock()
        socket_token = rt.client.post("/api/v4/socket_token?plan_id=p-1").json()["token"]

        with pytest.raises(resource_tokens.ResourceTokenError):
            resource_tokens.verify(socket_token, resource_tokens.PURPOSE_IMAGE, "")


# ---------------------------------------------------------------------------
# /upload_team_config
# ---------------------------------------------------------------------------
class TestUploadTeamConfig:
    def _file(self, content=b'{"name": "t", "status": "active"}', name="team.json"):
        return {"file": (name, content, "application/json")}

    def test_no_user(self, rt):
        _no_user(rt)
        resp = rt.client.post("/api/v4/upload_team_config", files=self._file())
        assert resp.status_code == 400

    def test_non_json_file(self, rt):
        resp = rt.client.post(
            "/api/v4/upload_team_config", files=self._file(name="team.txt")
        )
        assert resp.status_code == 400

    def test_invalid_json(self, rt):
        resp = rt.client.post(
            "/api/v4/upload_team_config", files=self._file(content=b"not json")
        )
        assert resp.status_code == 400

    def test_rai_validation_failure(self, rt):
        rt.rai_validate_team_config.return_value = (False, "unsafe content")
        resp = rt.client.post("/api/v4/upload_team_config", files=self._file())
        assert resp.status_code == 400

    def test_model_validation_failure(self, rt):
        rt.rai_validate_team_config.return_value = (True, None)
        rt.team_service.validate_team_models.return_value = (False, ["gpt-4"])
        resp = rt.client.post("/api/v4/upload_team_config", files=self._file())
        assert resp.status_code == 400

    def test_search_validation_failure(self, rt):
        rt.rai_validate_team_config.return_value = (True, None)
        rt.team_service.validate_team_models.return_value = (True, [])
        rt.team_service.validate_team_search_indexes.return_value = (False, ["idx err"])
        resp = rt.client.post("/api/v4/upload_team_config", files=self._file())
        assert resp.status_code == 400

    def test_success(self, rt):
        rt.rai_validate_team_config.return_value = (True, None)
        rt.team_service.validate_team_models.return_value = (True, [])
        rt.team_service.validate_team_search_indexes.return_value = (True, [])
        team_conf = MagicMock()
        team_conf.agents = [1]
        team_conf.starting_tasks = [1]
        team_conf.name = "MyTeam"
        team_conf.model_dump.return_value = {"name": "MyTeam"}
        rt.team_service.validate_and_parse_team_config.return_value = team_conf
        rt.team_service.save_team_configuration.return_value = "team-999"
        resp = rt.client.post("/api/v4/upload_team_config", files=self._file())
        assert resp.status_code == 200
        assert resp.json()["team_id"] == "team-999"

    def _parsed_team(self, rt, name="MyTeam"):
        team_conf = MagicMock()
        team_conf.agents = []
        team_conf.starting_tasks = []
        team_conf.name = name
        team_conf.model_dump.return_value = {"name": name}
        rt.team_service.validate_and_parse_team_config.return_value = team_conf
        return team_conf

    def test_success_with_team_id(self, rt):
        """An update goes through update_team_configuration, never an insert.

        Inserting the parsed document would land a duplicate of the same
        team_id in a different Cosmos partition, because its session_id — the
        partition key — is freshly generated on every parse.
        """
        rt.team_service.validate_team_models.return_value = (True, [])
        rt.team_service.validate_team_search_indexes.return_value = (True, [])
        self._parsed_team(rt)
        rt.team_service.update_team_configuration = AsyncMock(return_value="given-id")
        resp = rt.client.post(
            "/api/v4/upload_team_config?team_id=given-id", files=self._file()
        )
        assert resp.status_code == 200
        assert resp.json()["team_id"] == "given-id"
        rt.team_service.update_team_configuration.assert_awaited_once()
        rt.team_service.save_team_configuration.assert_not_called()

    def test_update_of_unknown_team_is_404(self, rt):
        rt.team_service.validate_team_models.return_value = (True, [])
        rt.team_service.validate_team_search_indexes.return_value = (True, [])
        self._parsed_team(rt)
        rt.team_service.update_team_configuration = AsyncMock(
            side_effect=LookupError("Team configuration 'nope' not found")
        )
        resp = rt.client.post(
            "/api/v4/upload_team_config?team_id=nope", files=self._file()
        )
        assert resp.status_code == 404

    def test_update_of_a_default_team_is_403(self, rt):
        """Shared default teams are visible to everyone and editable by no one."""
        rt.team_service.validate_team_models.return_value = (True, [])
        rt.team_service.validate_team_search_indexes.return_value = (True, [])
        self._parsed_team(rt)
        rt.team_service.update_team_configuration = AsyncMock(
            side_effect=PermissionError("shared default")
        )
        resp = rt.client.post(
            "/api/v4/upload_team_config?team_id=00000000-0000-0000-0000-000000000001",
            files=self._file(),
        )
        assert resp.status_code == 403

    def test_rai_validation_runs_even_when_a_team_id_is_supplied(self, rt):
        """Supplying a team_id must not skip content safety.

        The check was guarded by `if not team_id:`, so `?team_id=anything`
        bypassed it entirely — and a team configuration carries each agent's
        system_message, the very content this screens before it reaches an
        agent's system prompt.
        """
        rt.rai_validate_team_config.return_value = (False, "unsafe content")
        resp = rt.client.post(
            "/api/v4/upload_team_config?team_id=given-id", files=self._file()
        )
        assert resp.status_code == 400
        rt.rai_validate_team_config.assert_called_once()

    def test_parse_value_error(self, rt):
        rt.team_service.validate_team_models.return_value = (True, [])
        rt.team_service.validate_team_search_indexes.return_value = (True, [])
        rt.team_service.validate_and_parse_team_config = AsyncMock(
            side_effect=ValueError("bad config")
        )
        resp = rt.client.post("/api/v4/upload_team_config", files=self._file())
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# /team_configs (GET all)
# ---------------------------------------------------------------------------
class TestGetTeamConfigs:
    def test_no_user(self, rt):
        _no_user(rt)
        resp = rt.client.get("/api/v4/team_configs")
        assert resp.status_code == 401

    def test_success(self, rt):
        c = MagicMock()
        c.model_dump.return_value = {"id": "1"}
        rt.team_service.get_all_team_configurations.return_value = [c]
        resp = rt.client.get("/api/v4/team_configs")
        assert resp.status_code == 200
        assert resp.json() == [{"id": "1"}]

    def test_error(self, rt):
        rt.team_service.get_all_team_configurations = AsyncMock(
            side_effect=Exception("boom")
        )
        resp = rt.client.get("/api/v4/team_configs")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# /team_configs/{team_id}
# ---------------------------------------------------------------------------
class TestGetTeamConfigById:
    def test_no_user(self, rt):
        _no_user(rt)
        resp = rt.client.get("/api/v4/team_configs/t1")
        assert resp.status_code == 401

    def test_not_found(self, rt):
        rt.team_service.get_team_configuration.return_value = None
        resp = rt.client.get("/api/v4/team_configs/t1")
        assert resp.status_code == 404

    def test_success(self, rt):
        conf = MagicMock()
        conf.model_dump.return_value = {"id": "t1"}
        rt.team_service.get_team_configuration.return_value = conf
        resp = rt.client.get("/api/v4/team_configs/t1")
        assert resp.status_code == 200
        assert resp.json() == {"id": "t1"}

    def test_error(self, rt):
        rt.team_service.get_team_configuration = AsyncMock(
            side_effect=Exception("boom")
        )
        resp = rt.client.get("/api/v4/team_configs/t1")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# DELETE /team_configs/{team_id}
# ---------------------------------------------------------------------------
class TestDeleteTeamConfig:
    def test_no_user(self, rt):
        _no_user(rt)
        resp = rt.client.delete("/api/v4/team_configs/t1")
        assert resp.status_code == 401

    def test_not_found(self, rt):
        rt.team_service.delete_team_configuration.return_value = False
        resp = rt.client.delete("/api/v4/team_configs/t1")
        assert resp.status_code == 404

    def test_success(self, rt):
        rt.team_service.delete_team_configuration.return_value = True
        resp = rt.client.delete("/api/v4/team_configs/t1")
        assert resp.status_code == 200
        assert resp.json()["team_id"] == "t1"

    def test_error(self, rt):
        rt.team_service.delete_team_configuration = AsyncMock(
            side_effect=Exception("boom")
        )
        resp = rt.client.delete("/api/v4/team_configs/t1")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# /select_team
# ---------------------------------------------------------------------------
class TestSelectTeam:
    def test_no_user(self, rt):
        _no_user(rt)
        resp = rt.client.post("/api/v4/select_team", json={"team_id": "t1"})
        assert resp.status_code == 401

    def test_missing_team_id(self, rt):
        resp = rt.client.post("/api/v4/select_team", json={"team_id": ""})
        assert resp.status_code == 400

    def test_team_not_found(self, rt):
        rt.team_service.get_team_configuration.return_value = None
        resp = rt.client.post("/api/v4/select_team", json={"team_id": "t1"})
        assert resp.status_code == 404

    def test_selection_failed(self, rt):
        conf = MagicMock()
        conf.name = "TeamA"
        rt.team_service.get_team_configuration.return_value = conf
        rt.team_service.handle_team_selection.return_value = None
        resp = rt.client.post("/api/v4/select_team", json={"team_id": "t1"})
        assert resp.status_code == 404

    def test_success(self, rt):
        conf = MagicMock()
        conf.name = "TeamA"
        conf.agents = [1, 2]
        conf.description = "desc"
        rt.team_service.get_team_configuration.return_value = conf
        rt.team_service.handle_team_selection.return_value = MagicMock()
        resp = rt.client.post("/api/v4/select_team", json={"team_id": "t1"})
        assert resp.status_code == 200
        assert resp.json()["team_id"] == "t1"

    def test_error(self, rt):
        rt.team_service.get_team_configuration = AsyncMock(
            side_effect=Exception("boom")
        )
        resp = rt.client.post("/api/v4/select_team", json={"team_id": "t1"})
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# /plans
# ---------------------------------------------------------------------------
class TestGetPlans:
    def test_no_user(self, rt):
        _no_user(rt)
        resp = rt.client.get("/api/v4/plans")
        assert resp.status_code == 400

    def test_no_current_team(self, rt):
        rt.store.get_current_team.return_value = None
        resp = rt.client.get("/api/v4/plans")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_success(self, rt):
        current = MagicMock()
        current.team_id = "t1"
        rt.store.get_current_team.return_value = current
        rt.store.get_all_plans_by_team_id_status.return_value = []
        resp = rt.client.get("/api/v4/plans")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# /plan
# ---------------------------------------------------------------------------
class TestGetPlanById:
    def test_no_user(self, rt):
        _no_user(rt)
        resp = rt.client.get("/api/v4/plan?plan_id=p1")
        assert resp.status_code == 400

    def test_no_plan_id(self, rt):
        """Omitting plan_id is a 400, not the 500 the catch-all used to give."""
        resp = rt.client.get("/api/v4/plan")
        assert resp.status_code == 400

    def test_plan_not_found(self, rt):
        """A plan that does not resolve is a 404, not a 500.

        Since the lookup is user-scoped, this is also the response for a plan
        belonging to someone else.
        """
        rt.store.get_plan_by_plan_id.return_value = None
        resp = rt.client.get("/api/v4/plan?plan_id=p1")
        assert resp.status_code == 404

    def test_success(self, rt):
        plan = MagicMock()
        plan.session_id = "sess-1"
        plan.team_id = "t1"
        plan.plan_id = "p1"
        plan.m_plan = {"x": 1}
        plan.streaming_message = "streaming"
        rt.store.get_plan_by_plan_id.return_value = plan
        rt.store.get_team_by_id.return_value = MagicMock()
        rt.store.get_agent_messages.return_value = []
        resp = rt.client.get("/api/v4/plan?plan_id=p1")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# /images/{blob_name}
# ---------------------------------------------------------------------------
class TestGetGeneratedImage:
    def test_storage_not_configured(self, rt, monkeypatch):
        cfg = MagicMock()
        cfg.AZURE_STORAGE_BLOB_URL = ""
        monkeypatch.setattr(router_mod, "config", cfg)
        resp = rt.client.get("/api/v4/images/pic.png")
        assert resp.status_code == 503

    def test_invalid_name(self, rt, monkeypatch):
        cfg = MagicMock()
        cfg.AZURE_STORAGE_BLOB_URL = "https://blob"
        cfg.AZURE_STORAGE_IMAGES_CONTAINER = "images"
        monkeypatch.setattr(router_mod, "config", cfg)
        resp = rt.client.get("/api/v4/images/evil!.png")
        assert resp.status_code == 400

    def _configure_storage(self, rt, monkeypatch):
        cfg = MagicMock()
        cfg.AZURE_STORAGE_BLOB_URL = "https://blob"
        cfg.AZURE_STORAGE_IMAGES_CONTAINER = "images"
        # Every flag this endpoint reads must be set explicitly. A MagicMock
        # answers any attribute with a truthy Mock, so an unset flag silently
        # takes its enabled branch and the test asserts the opposite of what it
        # reads as.
        cfg.IMAGE_REQUIRE_OWNERSHIP_RECORD = False
        monkeypatch.setattr(router_mod, "config", cfg)

    def test_an_invalid_token_is_rejected(self, rt, monkeypatch):
        self._configure_storage(rt, monkeypatch)
        resp = rt.client.get("/api/v4/images/pic.png?token=nonsense")
        assert resp.status_code == 403

    def test_an_image_owned_by_another_user_is_refused(self, rt, monkeypatch):
        """The ownership record is what makes this more than 'some valid user'."""
        resource_tokens = router_mod.resource_tokens
        self._configure_storage(rt, monkeypatch)
        rt.store.get_image_asset = AsyncMock(return_value=MagicMock(user_id="someone-else"))
        token = resource_tokens.mint(resource_tokens.PURPOSE_IMAGE, "", "user-1", 60)

        resp = rt.client.get(f"/api/v4/images/pic.png?token={token}")

        assert resp.status_code == 403

    def test_an_image_with_no_record_falls_back_to_token_only(self, rt, monkeypatch):
        """Images generated before ownership was recorded must keep rendering."""
        resource_tokens = router_mod.resource_tokens
        self._configure_storage(rt, monkeypatch)
        rt.store.get_image_asset = AsyncMock(return_value=None)
        token = resource_tokens.mint(resource_tokens.PURPOSE_IMAGE, "", "user-1", 60)

        resp = rt.client.get(f"/api/v4/images/pic.png?token={token}")

        # Reaches blob storage (which is unreachable here) rather than 403ing.
        assert resp.status_code != 403

    def test_an_image_with_no_record_is_refused_once_enforcement_is_on(
        self, rt, monkeypatch
    ):
        """After the backfill, IMAGE_REQUIRE_OWNERSHIP_RECORD closes the fallback."""
        resource_tokens = router_mod.resource_tokens
        self._configure_storage(rt, monkeypatch)
        monkeypatch.setattr(
            router_mod.config, "IMAGE_REQUIRE_OWNERSHIP_RECORD", True, raising=False
        )
        rt.store.get_image_asset = AsyncMock(return_value=None)
        token = resource_tokens.mint(resource_tokens.PURPOSE_IMAGE, "", "user-1", 60)

        resp = rt.client.get(f"/api/v4/images/pic.png?token={token}")

        assert resp.status_code == 403

    def test_enforcement_does_not_disturb_an_image_the_caller_owns(
        self, rt, monkeypatch
    ):
        """Turning it on must deny only the unowned, not everything."""
        resource_tokens = router_mod.resource_tokens
        self._configure_storage(rt, monkeypatch)
        monkeypatch.setattr(
            router_mod.config, "IMAGE_REQUIRE_OWNERSHIP_RECORD", True, raising=False
        )
        rt.store.get_image_asset = AsyncMock(return_value=MagicMock(user_id="user-1"))
        token = resource_tokens.mint(resource_tokens.PURPOSE_IMAGE, "", "user-1", 60)

        resp = rt.client.get(f"/api/v4/images/pic.png?token={token}")

        assert resp.status_code != 403


# ---------------------------------------------------------------------------
# WebSocket /socket/{process_id}
# ---------------------------------------------------------------------------
class TestWebSocket:
    def test_connect_and_disconnect(self, rt):
        plan = MagicMock()
        plan.session_id = "sess-1"
        rt.store.get_plan_by_plan_id.return_value = plan
        with rt.client.websocket_connect(
            "/api/v4/socket/proc-1?user_id=user-1"
        ) as ws:
            ws.send_text("hello")
        rt.connection_config.add_connection.assert_called_once()

    def test_connect_with_a_valid_token_is_accepted(self, rt):
        """A signed token establishes identity rather than merely asserting it."""
        resource_tokens = router_mod.resource_tokens

        plan = MagicMock()
        plan.session_id = "sess-1"
        rt.store.get_plan_by_plan_id.return_value = plan
        token = resource_tokens.mint(
            resource_tokens.PURPOSE_SOCKET, "proc-1", "user-1", 60
        )

        with rt.client.websocket_connect(
            f"/api/v4/socket/proc-1?token={token}"
        ) as ws:
            ws.send_text("hello")
        rt.connection_config.add_connection.assert_called_once()

    def test_connect_with_a_token_for_another_plan_is_refused(self, rt):
        """Binding the token to a plan is what stops it being replayed."""
        resource_tokens = router_mod.resource_tokens

        plan = MagicMock()
        plan.session_id = "sess-1"
        rt.store.get_plan_by_plan_id.return_value = plan
        token = resource_tokens.mint(
            resource_tokens.PURPOSE_SOCKET, "some-other-plan", "user-1", 60
        )

        with pytest.raises(Exception):
            with rt.client.websocket_connect(f"/api/v4/socket/proc-1?token={token}"):
                pass
        rt.connection_config.add_connection.assert_not_called()

    def test_connect_with_a_garbage_token_is_refused(self, rt):
        with pytest.raises(Exception):
            with rt.client.websocket_connect("/api/v4/socket/proc-1?token=nonsense"):
                pass
        rt.connection_config.add_connection.assert_not_called()

    def test_connect_without_user_id_is_refused(self, rt):
        """No identity, no socket — there is no anonymous default any more."""
        with pytest.raises(Exception):
            with rt.client.websocket_connect("/api/v4/socket/proc-2"):
                pass
        rt.connection_config.add_connection.assert_not_called()

    def test_connect_to_a_plan_you_do_not_own_is_refused(self, rt):
        """The plan lookup is user-scoped, so a foreign plan_id does not resolve.

        This socket streams the whole orchestration — agent reasoning, tool
        calls, plan content, final result — so the handshake is refused rather
        than accepted and then dropped.
        """
        rt.store.get_plan_by_plan_id.return_value = None
        with pytest.raises(Exception):
            with rt.client.websocket_connect(
                "/api/v4/socket/someone-elses-plan?user_id=user-1"
            ):
                pass
        rt.connection_config.add_connection.assert_not_called()

    def test_connect_is_refused_when_the_check_cannot_run(self, rt):
        """An authorization check that errors has not passed — fail closed."""
        rt.store.get_plan_by_plan_id.side_effect = RuntimeError("cosmos down")
        with pytest.raises(Exception):
            with rt.client.websocket_connect(
                "/api/v4/socket/proc-1?user_id=user-1"
            ):
                pass
        rt.connection_config.add_connection.assert_not_called()
