"""samples/lattice/objects_cli.py -- unit-tests the command handlers
against a mocked `anduril.Lattice` client (matching the real SDK's
Objects client method signatures, verified against the installed SDK),
not a live Lattice environment. Requires anduril-lattice-sdk installed
(requirements-lattice.txt); skipped otherwise, same pattern as the
existing tests/test_lattice_adapter.py.
"""

import argparse
import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytest.importorskip("anduril")

_SPEC = importlib.util.spec_from_file_location(
    "objects_cli", Path(__file__).resolve().parent.parent / "samples" / "lattice" / "objects_cli.py"
)
assert _SPEC is not None and _SPEC.loader is not None
objects_cli = importlib.util.module_from_spec(_SPEC)
sys.modules["objects_cli"] = objects_cli
_SPEC.loader.exec_module(objects_cli)


def _args(**overrides) -> argparse.Namespace:
    base = {
        "lattice_endpoint": "lattice-test.example.com",
        "lattice_client_id": "id",
        "lattice_client_secret": "secret",
        "sandboxes_token": "",
        "extra_query": [],
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def test_upload_calls_the_sdk_with_an_open_file_handle(tmp_path, monkeypatch):
    mock_client = MagicMock()
    read_bytes = {}

    def _capture_upload(*, object_path, request, request_options=None):
        read_bytes["content"] = request.read()
        return MagicMock(size_bytes=42, expiry_time=None)

    mock_client.objects.upload_object.side_effect = _capture_upload
    monkeypatch.setattr(objects_cli, "_client", lambda args: mock_client)

    local_file = tmp_path / "photo.png"
    local_file.write_bytes(b"fake-image-bytes")

    args = _args(local_path=str(local_file), object_path="uploaded/photo.png")
    assert objects_cli.cmd_upload(args) == 0

    assert mock_client.objects.upload_object.call_args.kwargs["object_path"] == "uploaded/photo.png"
    assert read_bytes["content"] == b"fake-image-bytes"


def test_download_writes_streamed_chunks_to_the_local_path(tmp_path, monkeypatch):
    mock_client = MagicMock()
    mock_client.objects.get_object.return_value = iter([b"chunk-one-", b"chunk-two"])
    monkeypatch.setattr(objects_cli, "_client", lambda args: mock_client)

    out_path = tmp_path / "nested" / "out.bin"
    args = _args(object_path="uploaded/photo.png", local_path=str(out_path))
    assert objects_cli.cmd_download(args) == 0

    assert out_path.read_bytes() == b"chunk-one-chunk-two"
    assert mock_client.objects.get_object.call_args.kwargs["object_path"] == "uploaded/photo.png"


def test_metadata_prints_every_field(monkeypatch, capsys):
    mock_client = MagicMock()
    mock_client.objects.get_object_metadata.return_value = {"size_bytes": "42", "content_type": "image/png"}
    monkeypatch.setattr(objects_cli, "_client", lambda args: mock_client)

    args = _args(object_path="uploaded/photo.png")
    assert objects_cli.cmd_metadata(args) == 0

    out = capsys.readouterr().out
    assert "size_bytes: 42" in out
    assert "content_type: image/png" in out


def test_list_paginates_and_prints_a_summary_count(monkeypatch, capsys):
    mock_client = MagicMock()
    item1 = MagicMock(size_bytes=10, last_updated_at="t1")
    item1.content_identifier.path = "a/one.png"
    item2 = MagicMock(size_bytes=20, last_updated_at="t2")
    item2.content_identifier.path = "a/two.png"
    mock_client.objects.list_objects.return_value = [item1, item2]
    monkeypatch.setattr(objects_cli, "_client", lambda args: mock_client)

    args = _args(prefix="a/", max_page_size=None)
    assert objects_cli.cmd_list(args) == 0

    out = capsys.readouterr().out
    assert "a/one.png" in out
    assert "a/two.png" in out
    assert "2 object(s)" in out
    assert mock_client.objects.list_objects.call_args.kwargs["prefix"] == "a/"


def test_delete_calls_the_sdk_with_the_object_path(monkeypatch):
    mock_client = MagicMock()
    monkeypatch.setattr(objects_cli, "_client", lambda args: mock_client)

    args = _args(object_path="uploaded/photo.png")
    assert objects_cli.cmd_delete(args) == 0

    assert mock_client.objects.delete_object.call_args.kwargs["object_path"] == "uploaded/photo.png"


def test_extra_query_becomes_additional_query_parameters():
    args = _args(extra_query=["ttl=3600", "region=us-west"])
    assert objects_cli._request_options(args) == {
        "additional_query_parameters": {"ttl": "3600", "region": "us-west"}
    }


def test_no_extra_query_returns_none():
    args = _args(extra_query=[])
    assert objects_cli._request_options(args) is None
