from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.storage.volume import VolumeStore, VolumeStoreError


def _settings() -> Settings:
    return Settings(
        app_env="test",
        databricks_host="https://workspace.databricks.com",
        databricks_client_id="client-id",
        databricks_client_secret="client-secret",
        databricks_volume_path="/Volumes/prod_operations/rag/rag_documents",
    )


class FakeStream:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self._offset = 0

    def __enter__(self) -> FakeStream:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        if self._offset >= len(self._data):
            return b""
        if size < 0:
            result = self._data[self._offset :]
            self._offset = len(self._data)
            return result
        result = self._data[self._offset : self._offset + size]
        self._offset += len(result)
        return result


class FakeDownload:
    def __init__(self, data: bytes) -> None:
        self.contents = FakeStream(data)


class FakeFiles:
    def __init__(self) -> None:
        self.directories: list[str] = []
        self.uploads: dict[str, bytes] = {}

    def create_directory(self, path: str) -> None:
        self.directories.append(path)

    def upload(self, path: str, contents: object, overwrite: bool = False) -> None:
        self.uploads[path] = contents.read()

    def download(self, path: str) -> FakeDownload:
        return FakeDownload(self.uploads[path])

    def delete(self, path: str) -> None:
        self.uploads.pop(path, None)

    def get_metadata(self, path: str) -> None:
        if path not in self.uploads:
            raise FileNotFoundError(path)

    def list_directory_contents(self, path: str) -> list[SimpleNamespace]:
        prefix = f"{path}/"
        return [
            SimpleNamespace(path=key)
            for key in self.uploads
            if key.startswith(prefix) and "/" not in key[len(prefix) :]
        ]


def test_local_volume_store_round_trip(tmp_path: Path) -> None:
    store = VolumeStore(_settings(), mode="local")
    store._local_root = tmp_path
    asyncio.run(store.ensure_directories())
    written = asyncio.run(store.write_bytes("documents/a.txt", b"hello"))
    assert written == "/Volumes/prod_operations/rag/rag_documents/documents/a.txt"
    assert asyncio.run(store.read_bytes("documents/a.txt")) == b"hello"
    assert asyncio.run(store.exists("documents/a.txt")) is True
    assert asyncio.run(store.list("documents")) == ["a.txt"]
    asyncio.run(store.delete("documents/a.txt"))
    assert asyncio.run(store.exists("documents/a.txt")) is False


def test_sdk_volume_store_round_trip() -> None:
    files = FakeFiles()
    client = SimpleNamespace(files=files)
    store = VolumeStore(_settings(), client=client, mode="sdk")
    asyncio.run(store.ensure_directories())
    asyncio.run(store.write_bytes("document_versions/v1/source.pdf", b"pdf"))
    assert files.uploads[
        "/Volumes/prod_operations/rag/rag_documents/document_versions/v1/source.pdf"
    ] == b"pdf"
    assert asyncio.run(store.read_bytes("document_versions/v1/source.pdf")) == b"pdf"
    assert asyncio.run(store.list("document_versions/v1")) == ["source.pdf"]


def test_volume_store_rejects_traversal() -> None:
    store = VolumeStore(_settings(), mode="local")
    with pytest.raises(VolumeStoreError):
        store.path("../secrets")
