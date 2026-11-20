from __future__ import annotations

import asyncio
import io
import os
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from app.config import Settings
from app.logging_conf import get_logger

logger = get_logger(__name__)


class VolumeStoreError(ValueError):
    pass


class VolumeStore:
    DIRECTORIES: tuple[str, ...] = (
        "documents",
        "document_versions",
        "rendered_pages",
        "temporary",
    )

    def __init__(
        self,
        settings: Settings,
        client: Any | None = None,
        mode: Literal["auto", "local", "sdk"] = "auto",
    ) -> None:
        root = (settings.databricks_volume_path or "").strip().rstrip("/")
        if not root.startswith("/Volumes/"):
            raise VolumeStoreError(
                "DATABRICKS_VOLUME_PATH must start with /Volumes/<catalog>/<schema>/<volume>"
            )
        self._root = root
        self._local_root = Path(root)
        self._settings = settings
        self._client = client
        if mode == "sdk":
            self._mode = "sdk"
        elif mode == "local":
            self._mode = "local"
        elif client is not None or os.environ.get("DATABRICKS_RUNTIME_VERSION"):
            self._mode = "sdk"
        else:
            self._mode = "local" if self._local_root.exists() else "sdk"

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def root(self) -> str:
        return self._root

    async def ensure_directories(self) -> None:
        if self._mode == "local":
            await asyncio.to_thread(self._ensure_local_directories)
        else:
            await asyncio.to_thread(self._ensure_sdk_directories)

    async def write_bytes(self, relative_path: str, data: bytes) -> str:
        path = self._sanitize(relative_path)
        full_path = self.path(path)
        if self._mode == "local":
            def _write() -> None:
                target = self._local_root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)

            await asyncio.to_thread(_write)
        else:
            def _upload() -> None:
                self._ensure_sdk_parent(path)
                self._files.upload(full_path, io.BytesIO(data), overwrite=True)

            await asyncio.to_thread(_upload)
        return full_path

    async def read_bytes(self, relative_path: str) -> bytes:
        path = self._sanitize(relative_path)
        if self._mode == "local":
            return await asyncio.to_thread((self._local_root / path).read_bytes)

        def _download() -> bytes:
            response = self._files.download(self.path(path))
            with response.contents as stream:
                return stream.read()

        return await asyncio.to_thread(_download)

    async def delete(self, relative_path: str) -> None:
        path = self._sanitize(relative_path)
        if self._mode == "local":
            target = self._local_root / path
            await asyncio.to_thread(target.unlink, missing_ok=True)
        else:
            await asyncio.to_thread(self._files.delete, self.path(path))

    async def exists(self, relative_path: str) -> bool:
        path = self._sanitize(relative_path)
        if self._mode == "local":
            return await asyncio.to_thread((self._local_root / path).exists)

        def _metadata() -> bool:
            try:
                self._files.get_metadata(self.path(path))
            except Exception:
                return False
            return True

        return await asyncio.to_thread(_metadata)

    async def list(self, relative_path: str = "") -> list[str]:
        relative = self._sanitize(relative_path) if relative_path else ""
        if self._mode == "local":
            directory = self._local_root / relative if relative else self._local_root
            return await asyncio.to_thread(
                lambda: sorted(
                    entry.name for entry in directory.iterdir() if entry.is_file()
                )
            )

        path = self.path(relative) if relative else self._root

        def _list() -> list[str]:
            return sorted(
                item.path.rsplit("/", 1)[-1]
                for item in self._files.list_directory_contents(path)
            )

        return await asyncio.to_thread(_list)

    def path(self, relative_path: str = "") -> str:
        relative = self._sanitize(relative_path) if relative_path else ""
        return f"{self._root}/{relative}" if relative else self._root

    @property
    def _files(self) -> Any:
        if self._client is None:
            from databricks.sdk import WorkspaceClient

            secret = self._settings.databricks_secret_for("files") or getattr(
                self._settings, "databricks_client_secret", None
            )
            client_secret = (
                secret.get_secret_value() if secret is not None else ""
            )
            self._client = WorkspaceClient(
                host=self._settings.databricks_host,
                client_id=self._settings.databricks_client_id,
                client_secret=client_secret,
                oauth_scopes=[self._settings.databricks_files_oauth_scope],
            )
        return self._client.files

    def _sanitize(self, relative_path: str) -> str:
        candidate = PurePosixPath(relative_path.replace("\\", "/"))
        if candidate.is_absolute() or any(
            part in ("..", "~") for part in candidate.parts
        ):
            raise VolumeStoreError(f"Invalid volume path: {relative_path!r}")
        parts = [part for part in candidate.parts if part not in (".", "")]
        if not parts:
            raise VolumeStoreError("Volume path cannot be empty")
        return "/".join(parts)

    def _ensure_local_directories(self) -> None:
        for directory in self.DIRECTORIES:
            (self._local_root / directory).mkdir(parents=True, exist_ok=True)

    def _ensure_sdk_directories(self) -> None:
        for directory in self.DIRECTORIES:
            self._files.create_directory(f"{self._root}/{directory}")

    def _ensure_sdk_parent(self, relative_path: str) -> None:
        parts = relative_path.split("/")[:-1]
        current = self._root
        for part in parts:
            current = f"{current}/{part}"
            self._files.create_directory(current)


_store: VolumeStore | None = None


async def init_volume_store(settings: Settings) -> VolumeStore:
    global _store
    store = VolumeStore(settings)
    await store.ensure_directories()
    _store = store
    logger.info(
        "volume_store_initialized",
        mode=store.mode,
        root=store.root,
    )
    return store


def get_volume_store() -> VolumeStore:
    if _store is None:
        raise RuntimeError("Volume store has not been initialized")
    return _store


def set_volume_store(store: VolumeStore | None) -> None:
    global _store
    _store = store
