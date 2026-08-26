"""Tests for scripts/backfill_image_ownership.py.

The script cannot be exercised against a real Cosmos instance in CI, so the
store is faked. What is worth asserting is not the plumbing but the properties
that make the backfill safe to run and safe to act on afterwards: that it reads
across users, that it never reassigns an image, that it refuses to call an
empty read a success, and that it attributes each image to the user whose
message carried it.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "backfill_image_ownership.py"


def _load_from_path(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# `src/tests/backend/services/test_plan_service.py` replaces these in
# sys.modules with MagicMocks at import time and never restores them, so once
# that file has been collected every later import gets the mock. The script
# would bind a mocked `extract_blob_names` (which iterates as empty) and a
# mocked `DataType` (whose `.value` is a Mock, not "m_plan_message"), so the
# backfill would find no images, record nothing, and still exit 0. These tests
# passed in isolation and failed in the full suite, which is how it was found.
def _load_script():
    """Load the backfill script against the real modules, not other tests' mocks.

    The whole `common` namespace is cleared for the duration of this import,
    not just the two leaves the script names: the parent packages are stubbed
    too, and popping `common.models.messages` while leaving a MagicMock at
    `common.models` fails with "'common.models' is not a package". Everything
    is put back afterwards, so the tests relying on those stubs are unaffected.
    """
    saved = {
        name: module
        for name, module in sys.modules.items()
        if name == "common" or name.startswith("common.")
    }
    for name in saved:
        del sys.modules[name]
    try:
        return _load_from_path("backfill_image_ownership", SCRIPT)
    finally:
        sys.modules.update(saved)


backfill_module = _load_script()


class FakeMessage:
    def __init__(self, content="", raw_data="", user_id="", plan_id=""):
        self.content = content
        self.raw_data = raw_data
        self.user_id = user_id
        self.plan_id = plan_id


class FakeAsset:
    def __init__(self, blob_name, user_id):
        self.blob_name = blob_name
        self.user_id = user_id


class FakeStore:
    """Minimal stand-in recording the queries it was asked to run."""

    def __init__(self, messages, existing=None):
        self._messages = messages
        self._assets = dict(existing or {})
        self.queries = []

    async def query_items(self, query, parameters, model_class):
        self.queries.append((query, parameters))
        return list(self._messages)

    async def get_image_asset(self, blob_name):
        return self._assets.get(blob_name)

    async def add_image_asset(self, asset):
        # First writer wins, matching the real store's contract.
        self._assets.setdefault(asset.blob_name, asset)


BLOB_A = "11111111-1111-4111-8111-111111111111.png"
BLOB_B = "22222222-2222-4222-8222-222222222222.png"


def _url(blob):
    return f"/api/v4/images/{blob}"


@pytest.mark.asyncio
async def test_reads_across_users_not_just_one():
    """The query must not pin c.user_id, or it matches nothing and looks clean."""
    store = FakeStore([FakeMessage(content=_url(BLOB_A), user_id="alice")])

    await backfill_module.backfill(store, dry_run=True)

    query, parameters = store.queries[0]
    assert "c.user_id" not in query
    assert {"name": "@data_type", "value": "m_plan_message"} in parameters


@pytest.mark.asyncio
async def test_records_owner_from_the_message_that_carried_it():
    store = FakeStore([FakeMessage(content=_url(BLOB_A), user_id="alice")])

    assert await backfill_module.backfill(store, dry_run=False) == 0
    assert store._assets[BLOB_A].user_id == "alice"


@pytest.mark.asyncio
async def test_finds_images_that_only_appear_in_raw_data():
    """The live path scans the streamed payload too; stored, that is raw_data."""
    store = FakeStore([FakeMessage(raw_data=_url(BLOB_B), user_id="bob")])

    assert await backfill_module.backfill(store, dry_run=False) == 0
    assert store._assets[BLOB_B].user_id == "bob"


@pytest.mark.asyncio
async def test_never_reassigns_an_existing_owner():
    """A blob echoed by another user must not change hands."""
    store = FakeStore(
        [FakeMessage(content=_url(BLOB_A), user_id="mallory")],
        existing={BLOB_A: FakeAsset(BLOB_A, "alice")},
    )

    assert await backfill_module.backfill(store, dry_run=False) == 0
    assert store._assets[BLOB_A].user_id == "alice"


@pytest.mark.asyncio
async def test_dry_run_writes_nothing():
    store = FakeStore([FakeMessage(content=_url(BLOB_A), user_id="alice")])

    assert await backfill_module.backfill(store, dry_run=True) == 0
    assert store._assets == {}


@pytest.mark.asyncio
async def test_empty_read_is_not_reported_as_success():
    """query_items returns [] on failure, so an empty read must not exit 0."""
    store = FakeStore([])

    assert await backfill_module.backfill(store, dry_run=False) == 1


@pytest.mark.asyncio
async def test_image_on_a_message_with_no_user_is_left_unowned():
    """A guessed owner would deny the real one once enforcement is on."""
    store = FakeStore([FakeMessage(content=_url(BLOB_A), user_id="")])

    assert await backfill_module.backfill(store, dry_run=False) == 0
    assert store._assets == {}
