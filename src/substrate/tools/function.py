"""``@tool`` — a typed function becomes a ``Tool``.

::

    @tool(risk=ToolRisk.SAFE, idempotent=True)
    async def word_count(text: str) -> int:
        \"\"\"Count the words in a piece of text.

        Args:
            text: The text to count.
        \"\"\"
        return len(text.split())

The JSON schema the model sees comes from the signature (any type pydantic understands: ``str``, ``int``,
``Literal[...]``, ``Enum``, ``list[str]``, a ``BaseModel``…, with defaults), the description from the docstring's
first paragraph, and each parameter's description from its ``Args:`` entry. A parameter named ``ctx`` is not part of the
schema: it receives the run's context (deadline, cancellation) when the engine has one.

``risk`` and ``idempotent`` have no defaults — the engine acts on both (whether a human must approve a call; whether a
call that may or may not have happened can safely be made again), so writing a tool means deciding them.

The function may return text, a number, a ``dict``/list/model (sent as JSON), content blocks, or a full
``ToolExecutionResult``. A synchronous function runs on a worker thread so it never blocks the event loop.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import typing
from collections.abc import Callable
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    create_model,
)

from substrate.tools.protocols import ToolExecutionResult, ToolRisk
from substrate.types.content import (
    DataBlock,
    ErrorBlock,
    MediaBlock,
    ReasoningBlock,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UnknownBlock,
)

_CTX = "ctx"
_ANY: TypeAdapter[Any] = TypeAdapter(Any)
_BLOCKS = (
    TextBlock,
    DataBlock,
    ErrorBlock,
    ReasoningBlock,
    MediaBlock,
    ToolUseBlock,
    ToolResultBlock,
    UnknownBlock,
)
_ARGS_HEADER = re.compile(r"^\s*(Args|Arguments|Parameters):\s*$", re.IGNORECASE)
_ARG_LINE = re.compile(r"^\s{0,12}(\*{0,2}\w+)\s*(?:\([^)]*\))?\s*:\s*(.+)$")


def _parse_docstring(doc: str | None) -> tuple[str, dict[str, str]]:
    """The summary (first paragraph) and the ``Args:`` descriptions of a Google-style docstring."""
    if not doc:
        return "", {}
    lines = inspect.cleandoc(doc).splitlines()
    summary: list[str] = []
    for line in lines:
        if not line.strip() or _ARGS_HEADER.match(line):
            break
        summary.append(line.strip())
    described: dict[str, str] = {}
    in_args, current = False, None
    for line in lines:
        if _ARGS_HEADER.match(line):
            in_args, current = True, None
            continue
        if not in_args:
            continue
        if line.strip() and not line.startswith((" ", "\t")):
            break  # the next section
        match = _ARG_LINE.match(line)
        if match and (len(line) - len(line.lstrip())) <= 8:
            current = match.group(1).lstrip("*")
            described[current] = match.group(2).strip()
        elif current and line.strip():
            described[current] += " " + line.strip()
    return " ".join(summary), described


def _without_titles(node: Any) -> Any:
    """Pydantic titles every schema node; a model reading the schema gains nothing from them."""
    if isinstance(node, list):
        return [_without_titles(item) for item in node]
    if not isinstance(node, dict):
        return node
    clean: dict[str, Any] = {}
    for key, value in node.items():
        if key == "title" and isinstance(value, str):
            continue
        if key in ("properties", "$defs"):
            clean[key] = {name: _without_titles(child) for name, child in value.items()}
        else:
            clean[key] = _without_titles(value)
    return clean


def _as_result(name: str, value: Any) -> ToolExecutionResult:
    if isinstance(value, ToolExecutionResult):
        return value if value.name else value.model_copy(update={"name": name})
    if value is None:
        return ToolExecutionResult(name=name, content=[])
    if isinstance(value, str):
        return ToolExecutionResult(name=name, content=[TextBlock(text=value)])
    if isinstance(value, _BLOCKS):
        return ToolExecutionResult(name=name, content=[value])
    if (
        isinstance(value, list)
        and value
        and all(isinstance(item, _BLOCKS) for item in value)
    ):
        return ToolExecutionResult(name=name, content=list(value))
    if isinstance(value, (bool, int, float)):
        return ToolExecutionResult(name=name, content=[TextBlock(text=str(value))])
    return ToolExecutionResult(
        name=name,
        content=[
            TextBlock(
                text=json.dumps(_ANY.dump_python(value, mode="json", fallback=str))
            )
        ],
    )


class FunctionTool:
    """A ``Tool`` made from a function. Build one with ``@tool``."""

    def __init__(
        self,
        fn: Callable[..., Any],
        *,
        risk: ToolRisk,
        idempotent: bool,
        name: str | None = None,
        description: str | None = None,
        concurrency_safe: bool = False,
    ) -> None:
        signature = inspect.signature(fn)
        summary, argument_docs = _parse_docstring(fn.__doc__)
        self.name = name or fn.__name__
        self.description = description or summary
        if not self.description:
            raise ValueError(
                f"tool {self.name!r} needs a description — the model reads it: a docstring or description="
            )
        self.risk = ToolRisk(risk)
        self.idempotent = bool(idempotent)
        self.concurrency_safe = concurrency_safe
        self.fn = fn
        self._is_async = inspect.iscoroutinefunction(fn)
        self._wants_ctx = _CTX in signature.parameters

        hints = typing.get_type_hints(fn, include_extras=True)
        fields: dict[str, Any] = {}
        for param in signature.parameters.values():
            if param.name == _CTX:
                continue
            if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
                raise TypeError(
                    f"tool {self.name!r}: *args and **kwargs cannot be described to a model; name each parameter"
                )
            annotation = hints.get(param.name, Any)
            default = ... if param.default is param.empty else param.default
            fields[param.name] = (
                annotation,
                Field(default, description=argument_docs.get(param.name)),
            )
        self._arguments: type[BaseModel] = create_model(
            f"{self.name}_arguments",
            __config__=ConfigDict(extra="forbid", arbitrary_types_allowed=True),
            **fields,
        )
        self.input_schema: dict[str, Any] = _without_titles(
            self._arguments.model_json_schema()
        )
        self.input_schema.setdefault("properties", {})

    async def execute(self, *, ctx: Any = None, **kwargs: Any) -> ToolExecutionResult:
        try:
            parsed = self._arguments.model_validate(kwargs)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(map(str, e['loc'])) or 'arguments'}: {e['msg']}"
                for e in exc.errors()
            )
            return ToolExecutionResult(
                name=self.name,
                is_error=True,
                content=[TextBlock(text=f"invalid arguments — {problems}")],
            )
        arguments = {name: getattr(parsed, name) for name in type(parsed).model_fields}
        if self._wants_ctx:
            arguments[_CTX] = ctx
        if self._is_async:
            value = await self.fn(**arguments)
        else:
            value = await asyncio.to_thread(self.fn, **arguments)
        return _as_result(self.name, value)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        """The function itself, so a tool can still be called (and tested) as plain code."""
        return self.fn(*args, **kwargs)

    def __repr__(self) -> str:
        return f"<tool {self.name} risk={self.risk.value} idempotent={self.idempotent}>"


def tool(
    *,
    risk: ToolRisk,
    idempotent: bool,
    name: str | None = None,
    description: str | None = None,
    concurrency_safe: bool = False,
) -> Callable[[Callable[..., Any]], FunctionTool]:
    """Make the decorated function a ``Tool``. ``risk`` and ``idempotent`` are required; see the module docstring."""

    def decorate(fn: Callable[..., Any]) -> FunctionTool:
        return FunctionTool(
            fn,
            risk=risk,
            idempotent=idempotent,
            name=name,
            description=description,
            concurrency_safe=concurrency_safe,
        )

    return decorate


__all__ = ["FunctionTool", "tool"]
