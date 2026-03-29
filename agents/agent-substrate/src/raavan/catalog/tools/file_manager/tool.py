"""FileManagerTool — lets the agent list, read, and write files via FileStore.

Actions:
  list_files  – List files in the current thread (returns names + sizes).
  read_file   – Read (and optionally extract text from) a file by ID.
  write_file  – Write agent-generated content to a new file.
  get_url     – Get a pre-signed download URL for a file.
  delete_file – Remove a file from the thread.

The tool operates in the context of the current thread (via contextvars).
"""

from __future__ import annotations

import contextvars
import logging
import uuid
from typing import Any, ClassVar, Optional

from raavan.core.tools.base_tool import BaseTool, ToolResult, ToolRisk

logger = logging.getLogger(__name__)

# Set by chat route before agent.run_stream() — same pattern as TaskManagerTool
current_thread_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "file_manager_thread_id", default=""
)


def _text_result(
    text: str, *, is_error: bool = False, app_data: Optional[dict[str, Any]] = None
) -> ToolResult:
    return ToolResult(
        content=[{"type": "text", "text": text}],
        is_error=is_error,
        app_data=app_data,
    )


class FileManagerTool(BaseTool):
    """Agent-facing tool for file operations within a conversation thread.

    Delegates to ``FileStore`` for I/O and ``FileMetadata`` for DB tracking.
    Must be initialised with ``session_factory`` and ``file_store`` from
    ``ServerContext``.
    """

    risk: ClassVar[ToolRisk] = ToolRisk.SAFE

    def __init__(
        self,
        file_store: Any,
        session_factory: Any,
    ) -> None:
        super().__init__(
            name="file_manager",
            description=(
                "Manage files attached to the current conversation. "
                "Actions: list_files, read_file, write_file, get_url, delete_file."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "list_files",
                            "read_file",
                            "write_file",
                            "get_url",
                            "delete_file",
                        ],
                        "description": "The file operation to perform.",
                    },
                    "file_id": {
                        "type": "string",
                        "description": "UUID of the file (required for read_file, get_url, delete_file).",
                    },
                    "filename": {
                        "type": "string",
                        "description": "Name for the new file (required for write_file).",
                    },
                    "content": {
                        "type": "string",
                        "description": "Text content to write (required for write_file).",
                    },
                    "content_type": {
                        "type": "string",
                        "description": "MIME type for write_file (default: text/plain).",
                    },
                },
                "required": ["action"],
            },
        )
        self._store = file_store
        self._session_factory = session_factory

    async def execute(  # type: ignore[override]
        self,
        *,
        action: str,
        file_id: str = "",
        filename: str = "",
        content: str = "",
        content_type: str = "text/plain",
    ) -> ToolResult:
        thread_id_str = current_thread_id.get("")
        if not thread_id_str:
            return _text_result("Error: no active thread context", is_error=True)

        thread_id = uuid.UUID(thread_id_str)

        try:
            if action == "list_files":
                return await self._list_files(thread_id)
            elif action == "read_file":
                return await self._read_file(thread_id, file_id)
            elif action == "write_file":
                return await self._write_file(
                    thread_id,
                    filename,
                    content,
                    content_type,
                )
            elif action == "get_url":
                return await self._get_url(thread_id, file_id)
            elif action == "delete_file":
                return await self._delete_file(thread_id, file_id)
            else:
                return _text_result(f"Unknown action: {action}", is_error=True)
        except Exception as exc:
            logger.exception("FileManagerTool error: %s", exc)
            return _text_result(f"Error: {exc}", is_error=True)

    async def _list_files(self, thread_id: uuid.UUID) -> ToolResult:
        from raavan.server.services.file_service import list_files

        async with self._session_factory() as db:
            files = await list_files(db, thread_id)
            if not files:
                return _text_result("No files in this thread.")

            lines = [f"Files in thread ({len(files)}):"]
            for f in files:
                size_kb = f.size_bytes / 1024
                lines.append(
                    f"  - {f.original_name} (id={f.id}, {size_kb:.1f} KB, {f.content_type})"
                )

            return _text_result("\n".join(lines), app_data={"file_count": len(files)})

    async def _read_file(self, thread_id: uuid.UUID, file_id_str: str) -> ToolResult:
        from raavan.server.services.file_service import extract_text, get_file

        if not file_id_str:
            return _text_result(
                "Error: file_id is required for read_file", is_error=True
            )

        file_id = uuid.UUID(file_id_str)
        async with self._session_factory() as db:
            meta = await get_file(db, file_id, thread_id)
            if not meta:
                return _text_result(
                    f"File {file_id} not found in this thread.", is_error=True
                )

            text = await extract_text(self._store, meta)
            if text:
                return _text_result(
                    text,
                    app_data={
                        "file_id": str(meta.id),
                        "filename": meta.original_name,
                        "content_type": meta.content_type,
                    },
                )
            else:
                return _text_result(
                    f"File '{meta.original_name}' is binary and cannot be read as text. "
                    f"Use get_url to obtain a download link instead.",
                    app_data={
                        "file_id": str(meta.id),
                        "filename": meta.original_name,
                        "content_type": meta.content_type,
                    },
                )

    async def _write_file(
        self,
        thread_id: uuid.UUID,
        filename: str,
        content: str,
        content_type: str,
    ) -> ToolResult:
        from raavan.server.services.file_service import save_file
        from raavan.core.storage.tenant import FileScope

        if not filename:
            return _text_result(
                "Error: filename is required for write_file", is_error=True
            )
        if not content:
            return _text_result(
                "Error: content is required for write_file", is_error=True
            )

        data = content.encode("utf-8")
        async with self._session_factory() as db:
            meta = await save_file(
                db,
                self._store,
                thread_id=thread_id,
                name=filename,
                mime=content_type,
                content=data,
                scope=FileScope.GENERATED,
            )
            await db.commit()

            return _text_result(
                f"File '{filename}' created successfully ({len(data)} bytes).",
                app_data={
                    "file_id": str(meta.id),
                    "filename": meta.original_name,
                    "size_bytes": meta.size_bytes,
                },
            )

    async def _get_url(self, thread_id: uuid.UUID, file_id_str: str) -> ToolResult:
        from raavan.server.services.file_service import get_file, get_file_url

        if not file_id_str:
            return _text_result("Error: file_id is required for get_url", is_error=True)

        file_id = uuid.UUID(file_id_str)
        async with self._session_factory() as db:
            meta = await get_file(db, file_id, thread_id)
            if not meta:
                return _text_result(f"File {file_id} not found.", is_error=True)

            try:
                url = await get_file_url(self._store, meta)
                return _text_result(
                    f"Download URL for '{meta.original_name}': {url}",
                    app_data={"url": url, "file_id": str(meta.id)},
                )
            except NotImplementedError:
                return _text_result(
                    f"Direct URLs not available (encrypted store). "
                    f"File '{meta.original_name}' must be downloaded via the API.",
                    app_data={"file_id": str(meta.id)},
                )

    async def _delete_file(self, thread_id: uuid.UUID, file_id_str: str) -> ToolResult:
        from raavan.server.services.file_service import delete_file

        if not file_id_str:
            return _text_result(
                "Error: file_id is required for delete_file", is_error=True
            )

        file_id = uuid.UUID(file_id_str)
        async with self._session_factory() as db:
            deleted = await delete_file(db, self._store, file_id, thread_id)
            await db.commit()

            if deleted:
                return _text_result(f"File {file_id} deleted.")
            else:
                return _text_result(f"File {file_id} not found.", is_error=True)
