"""Service JSON -> the document library's value types (frozen dataclasses), driven by their annotations."""

from __future__ import annotations

import dataclasses
import types
import typing
from typing import Any, TypeVar

T = TypeVar("T")


def build(tp: Any, value: Any) -> Any:
    if value is None:
        return None
    origin = typing.get_origin(tp)
    if origin is typing.Union or origin is types.UnionType:
        args = [a for a in typing.get_args(tp) if a is not type(None)]
        return build(args[0], value) if len(args) == 1 else value
    if dataclasses.is_dataclass(tp):
        hints = typing.get_type_hints(tp)
        return tp(
            **{
                f.name: build(hints[f.name], value[f.name])
                for f in dataclasses.fields(tp)
                if f.name in value
            }
        )
    if origin is tuple:
        args = typing.get_args(tp)
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(build(args[0], v) for v in value)
        return tuple(build(a, v) for a, v in zip(args, value, strict=True))
    if origin is list:
        (arg,) = typing.get_args(tp)
        return [build(arg, v) for v in value]
    return value


def from_wire(cls: type[T], value: dict[str, Any]) -> T:
    return build(cls, value)
