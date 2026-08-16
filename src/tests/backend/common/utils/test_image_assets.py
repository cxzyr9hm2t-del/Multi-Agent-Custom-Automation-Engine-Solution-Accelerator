"""Unit tests for backend.common.utils.image_assets."""

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
if src_path not in sys.path:
    sys.path.insert(0, src_path)

os.environ.setdefault('APP_ENV', 'dev')

from backend.common.utils.image_assets import (  # noqa: E402
    extract_blob_names,
    record_image_ownership,
)

BLOB = "3f2504e0-4f89-11d3-9a0c-0305e82c3301.png"
OTHER = "8c9e6679-7425-40de-944b-e07fc1f90ae7.png"


class TestExtractBlobNames:
    def test_finds_a_blob_in_markdown(self):
        text = f"Here you go: ![Generated image](/api/v4/images/{BLOB})"
        assert extract_blob_names(text) == [BLOB]

    def test_finds_several_and_de_duplicates(self):
        text = (
            f"/api/v4/images/{BLOB} and /api/v4/images/{OTHER} "
            f"and again /api/v4/images/{BLOB}"
        )
        assert extract_blob_names(text) == [BLOB, OTHER]

    def test_absolute_urls_are_matched_too(self):
        text = f"https://backend.example.com/api/v4/images/{BLOB}"
        assert extract_blob_names(text) == [BLOB]

    @pytest.mark.parametrize("text", [
        "",
        None,
        "no images here",
        "/api/v4/images/not-a-uuid.png",
        "/api/v4/images/../../etc/passwd",
        f"/api/v4/other/{BLOB}",
    ])
    def test_non_matches_yield_nothing(self, text):
        """The pattern is strict so arbitrary text cannot claim a blob."""
        assert extract_blob_names(text) == []


class _RecordedAsset:
    """Stand-in for ImageAsset.

    Other test modules replace common.models.messages with a MagicMock, which
    would make the real ImageAsset a mock and hollow out these assertions.
    """

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


@pytest.fixture(autouse=True)
def _pin_model(monkeypatch):
    import backend.common.utils.image_assets as image_assets
    monkeypatch.setattr(image_assets, "ImageAsset", _RecordedAsset)


class TestRecordImageOwnership:
    def _store(self, existing=None):
        store = MagicMock()
        store.get_image_asset = AsyncMock(return_value=existing)
        store.add_image_asset = AsyncMock()
        return store

    @pytest.mark.asyncio
    async def test_records_an_unclaimed_image(self):
        store = self._store()
        await record_image_ownership(
            store, f"![img](/api/v4/images/{BLOB})", "alice", "plan-1"
        )

        store.add_image_asset.assert_awaited_once()
        recorded = store.add_image_asset.await_args[0][0]
        assert recorded.blob_name == BLOB
        assert recorded.user_id == "alice"
        assert recorded.plan_id == "plan-1"

    @pytest.mark.asyncio
    async def test_first_writer_wins(self):
        """A blob name echoed by another user must not reassign ownership."""
        existing = MagicMock(user_id="alice")
        store = self._store(existing=existing)

        await record_image_ownership(
            store, f"/api/v4/images/{BLOB}", "mallory", "plan-2"
        )

        store.add_image_asset.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_storage_failure_does_not_propagate(self):
        """Recording is best-effort; it must not break the message it came from."""
        store = self._store()
        store.add_image_asset = AsyncMock(side_effect=Exception("cosmos down"))

        await record_image_ownership(store, f"/api/v4/images/{BLOB}", "alice")

    @pytest.mark.asyncio
    async def test_no_user_records_nothing(self):
        store = self._store()
        await record_image_ownership(store, f"/api/v4/images/{BLOB}", "")
        store.add_image_asset.assert_not_called()

    @pytest.mark.asyncio
    async def test_text_without_images_touches_nothing(self):
        store = self._store()
        await record_image_ownership(store, "just a sentence", "alice")
        store.get_image_asset.assert_not_called()
        store.add_image_asset.assert_not_called()
