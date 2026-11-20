from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from typing import Any


class Jsonb:
    def __init__(self, value: Any) -> None:
        self.value = value


class DictRow(Mapping[str, Any]):
    def __init__(self, values: tuple[Any, ...], columns: list[str]) -> None:
        self._values = values
        self._columns = columns
        self._index = {name: position for position, name in enumerate(columns)}

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, int):
            return self._values[key]
        return self._values[self._index[key]]

    def __iter__(self) -> Iterator[str]:
        return iter(self._columns)

    def __len__(self) -> int:
        return len(self._columns)

    def get(self, key: str, default: Any = None) -> Any:
        return self._values[self._index[key]] if key in self._index else default

    def keys(self) -> list[str]:
        return list(self._columns)

    def values(self) -> tuple[Any, ...]:
        return self._values

    def items(self) -> Iterator[tuple[str, Any]]:
        for name in self._columns:
            yield name, self[name]


def serialize_json_value(value: Any) -> Any:
    if isinstance(value, Jsonb):
        return json.dumps(value.value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, dict | list | tuple):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return value


def decode_json_value(value: Any) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith(("{", "[")):
            try:
                return json.loads(stripped)
            except ValueError:
                return value
    return value
