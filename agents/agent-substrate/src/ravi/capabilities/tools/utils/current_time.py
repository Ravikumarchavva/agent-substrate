"""CurrentTimeTool — return the current date, time, and timezone."""

from __future__ import annotations

import datetime

from ravi.kernel import TextBlock
from ravi.kernel.tools import ToolExecutionResult


class CurrentTimeTool:
    """Return the current date/time in UTC and a requested timezone.

    Example::

        from ravi.capabilities.tools import CurrentTimeTool
        agent = ReActAgent("bot", runtime, model=llm, tools=[CurrentTimeTool()])
    """

    name = "get_current_time"
    description = (
        "Return the current UTC date and time. "
        "Optionally convert to a named timezone (e.g. 'US/Eastern', 'Asia/Kolkata')."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "timezone": {
                "type": "string",
                "description": "IANA timezone name, e.g. 'Asia/Kolkata'. Defaults to UTC.",
            }
        },
        "additionalProperties": False,
    }

    async def execute(
        self, *, timezone: str = "UTC", **_: object
    ) -> ToolExecutionResult:
        try:
            import zoneinfo

            tz = zoneinfo.ZoneInfo(timezone)
            now = datetime.datetime.now(tz)
            text = now.strftime("%Y-%m-%d %H:%M:%S %Z (UTC%z)")
            return ToolExecutionResult(content=[TextBlock(text=text)])
        except Exception as exc:
            return ToolExecutionResult(
                content=[TextBlock(text=f"Error: {exc}")],
                is_error=True,
            )
