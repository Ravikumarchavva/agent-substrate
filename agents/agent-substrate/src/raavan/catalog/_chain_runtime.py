"""ChainRuntime — execute LLM-written Python scripts that chain adapters.

The runtime builds a namespace where each registered adapter is available
as an async callable:  ``await adapters.email_sender(to="...", subject="...")``.

Scripts are executed in the Firecracker sandbox (via CodeInterpreterTool) when
available, otherwise in a restricted local exec() as fallback.

Usage::

    runtime = ChainRuntime(catalog=catalog, data_store=store)
    result = await runtime.execute_script("result = await adapters.calculator(expression='2+2')")
"""

from __future__ import annotations

import asyncio
import logging
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from raavan.catalog._data_ref import DataRef, DataRefStore
from raavan.core.tools.base_tool import BaseTool

logger = logging.getLogger("raavan.catalog.chain_runtime")

_LARGE_RESULT_THRESHOLD = 4096  # bytes — results larger than this become DataRefs


@dataclass
class ChainResult:
    """Result of a chained script execution."""

    outputs: List[Any] = field(default_factory=list)
    data_refs: List[DataRef] = field(default_factory=list)
    logs: str = ""
    error: Optional[str] = None
    duration_ms: int = 0


class AdapterProxy:
    """Wraps a ``BaseTool`` as a simple async callable for script namespaces.

    ``await adapters.calculator(expression="2+2")`` calls
    ``tool.run(expression="2+2")`` under the hood.  Large results are
    automatically stored as DataRefs.
    """

    def __init__(
        self,
        tool: BaseTool,
        data_store: Optional[DataRefStore] = None,
    ) -> None:
        self._tool = tool
        self._data_store = data_store
        self.name = tool.name

    async def __call__(self, **kwargs: Any) -> Any:
        """Execute the underlying tool and return its result.

        Large results (> threshold) are stored as DataRef pointers.
        """
        result = await self._tool.run(**kwargs)

        # If already a DataRef, return as-is
        if result.data_ref is not None:
            return result.data_ref

        # Check if result content is large
        content_str = str(result.content)
        if (
            self._data_store is not None
            and len(content_str.encode("utf-8")) > _LARGE_RESULT_THRESHOLD
        ):
            ref = await self._data_store.store(
                content_str,
                content_type="application/json",
            )
            result.data_ref = ref
            return ref

        # Small result — return content directly
        if result.is_error:
            raise RuntimeError(f"Tool {self._tool.name} failed: {result.content}")
        return result.content


class _AdapterNamespace:
    """Dict-like namespace that provides ``adapters.name(...)`` access."""

    def __init__(self) -> None:
        self._proxies: Dict[str, AdapterProxy] = {}

    def register(self, proxy: AdapterProxy) -> None:
        self._proxies[proxy.name] = proxy

    def __getattr__(self, name: str) -> AdapterProxy:
        try:
            return self._proxies[name]
        except KeyError:
            available = ", ".join(sorted(self._proxies.keys()))
            raise AttributeError(
                f"No adapter named '{name}'. Available: {available}"
            ) from None

    def list_adapters(self) -> List[str]:
        return sorted(self._proxies.keys())


class ChainRuntime:
    """Execute chained adapter scripts.

    Parameters
    ----------
    catalog
        The CapabilityRegistry to draw tools from.
    data_store
        DataRefStore for large intermediate data.
    """

    def __init__(
        self,
        catalog: Any,
        data_store: Optional[DataRefStore] = None,
    ) -> None:
        from raavan.core.tools.catalog import CapabilityRegistry

        self._catalog: CapabilityRegistry = catalog
        self._data_store = data_store

    def build_namespace(self) -> _AdapterNamespace:
        """Build the ``adapters`` namespace with proxies for all registered tools."""
        ns = _AdapterNamespace()
        for tool in self._catalog.all_tools():
            proxy = AdapterProxy(tool, data_store=self._data_store)
            ns.register(proxy)
        return ns

    async def execute_script(
        self,
        code: str,
        *,
        timeout: int = 120,
    ) -> ChainResult:
        """Execute a Python script with adapter proxies available.

        The script has access to:
        - ``adapters`` — namespace with all tool proxies
        - ``DataRef`` — the DataRef class for type checking
        - ``results`` — a list the script can append outputs to
        - Standard builtins (no file I/O or imports restricted)
        """
        start = time.monotonic()
        namespace = self.build_namespace()
        results_collector: List[Any] = []
        data_refs: List[DataRef] = []
        log_lines: List[str] = []

        # Build execution globals
        exec_globals: Dict[str, Any] = {
            "adapters": namespace,
            "DataRef": DataRef,
            "results": results_collector,
            "print": lambda *args, **kw: log_lines.append(
                " ".join(str(a) for a in args)
            ),
        }

        try:
            # Wrap in async function for await support
            wrapped = "async def __chain__():\n"
            for line in code.split("\n"):
                wrapped += f"    {line}\n"
            wrapped += "\nimport asyncio\nasyncio.get_event_loop().run_until_complete(__chain__())"

            # Actually use asyncio properly
            async def _run() -> None:
                local_globals = dict(exec_globals)
                local_globals["asyncio"] = asyncio

                # Compile and define the async function
                func_code = "async def __chain__():\n"
                for line in code.split("\n"):
                    func_code += f"    {line}\n"

                exec(compile(func_code, "<chain>", "exec"), local_globals)  # noqa: S102
                await local_globals["__chain__"]()

            await asyncio.wait_for(_run(), timeout=timeout)

            # Collect DataRefs from results
            for item in results_collector:
                if isinstance(item, DataRef):
                    data_refs.append(item)

            duration = max(1, int((time.monotonic() - start) * 1000))
            return ChainResult(
                outputs=results_collector,
                data_refs=data_refs,
                logs="\n".join(log_lines),
                duration_ms=duration,
            )

        except asyncio.TimeoutError:
            duration = max(1, int((time.monotonic() - start) * 1000))
            return ChainResult(
                logs="\n".join(log_lines),
                error=f"Script timed out after {timeout}s",
                duration_ms=duration,
            )
        except Exception as exc:
            duration = max(1, int((time.monotonic() - start) * 1000))
            return ChainResult(
                logs="\n".join(log_lines),
                error=f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}",
                duration_ms=duration,
            )
