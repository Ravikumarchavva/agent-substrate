"""Convert service JSON dicts to the document library's value types."""

from __future__ import annotations

import dataclasses
import types
import typing
from typing import Any

from loamz.data.storage.documents.types import Hit, Hits


def _build(tp: Any, value: Any) -> Any:
    if value is None:
        return None
    if tp is Hits:
        hits = Hits([_build(Hit, h) for h in value.get("hits", [])])
        hits.note = value.get("note")
        return hits
    origin = typing.get_origin(tp)
    if origin is typing.Union or origin is types.UnionType:
        args = [a for a in typing.get_args(tp) if a is not type(None)]
        return _build(args[0], value) if len(args) == 1 else value
    if dataclasses.is_dataclass(tp):
        hints = typing.get_type_hints(tp)
        return tp(
            **{
                f.name: _build(hints[f.name], value[f.name])
                for f in dataclasses.fields(tp)
                if f.name in value
            }
        )
    if origin is tuple:
        args = typing.get_args(tp)
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_build(args[0], v) for v in value)
        return tuple(_build(a, v) for a, v in zip(args, value, strict=False))
    if origin is list:
        (arg,) = typing.get_args(tp)
        return [_build(arg, v) for v in value]
    return value


def from_wire[T](cls: type[T], value: dict[str, Any]) -> T:
    """Build a typed value from wire dict."""
    return _build(cls, value)
