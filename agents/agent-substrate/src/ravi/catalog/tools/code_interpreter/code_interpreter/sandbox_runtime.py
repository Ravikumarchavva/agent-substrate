from __future__ import annotations

import base64
import hashlib
import io
import mimetypes
import os
import shlex
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

BASE_DIR = "/app"
WORKSPACE_DIR = os.getenv("CODE_INTERPRETER_WORKSPACE", "/app/workspace")
ARTIFACTS_DIR = os.path.join(WORKSPACE_DIR, "artifacts")
MAX_INLINE_FILE_BYTES = int(
    os.getenv("CODE_INTERPRETER_MAX_INLINE_FILE_BYTES", str(10 * 1024 * 1024))
)

_session_globals: dict[str, Any] = {"__name__": "__code_interpreter__"}
_plotly_seen_hashes: dict[str, str] = {}  # keyed by variable name, not id()
_execution_lock = threading.RLock()
_artifact_counter = 0


class ExecuteRequest(BaseModel):
    command: str
    timeout: int = Field(default=60, ge=1)


class ExecuteResponse(BaseModel):
    stdout: str
    stderr: str
    exit_code: int


class RunCodeRequest(BaseModel):
    code: str
    include_artifacts: bool = True
    close_figures: bool = True


class UploadRequest(BaseModel):
    filename: str
    content_base64: str
    overwrite: bool = True


class PathRequest(BaseModel):
    path: str = "."


class ListRequest(BaseModel):
    path: str = "."
    recursive: bool = False


app = FastAPI(
    title="Code Interpreter Sandbox Runtime",
    description="Runtime API for stateful code execution and workspace artifacts.",
    version="2.0.0",
)


@app.on_event("startup")
async def _startup() -> None:
    os.makedirs(WORKSPACE_DIR, exist_ok=True)
    os.makedirs(ARTIFACTS_DIR, exist_ok=True)
    try:
        import matplotlib

        matplotlib.use("Agg", force=True)
    except Exception:
        pass


@app.get("/")
async def health_check() -> dict[str, str]:
    return {"status": "ok", "message": "Sandbox runtime is active."}


@app.post("/ci/run")
def ci_run(request: RunCodeRequest) -> dict[str, Any]:
    """Execute Python in a persistent interpreter and return changed files."""

    with _execution_lock:
        os.makedirs(WORKSPACE_DIR, exist_ok=True)
        os.makedirs(ARTIFACTS_DIR, exist_ok=True)
        _prepare_repl_globals()
        _install_plotly_show_hook()

        before = _snapshot_workspace()
        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        old_stdout, old_stderr = sys.stdout, sys.stderr
        old_cwd = os.getcwd()
        exit_code = 0

        try:
            os.chdir(WORKSPACE_DIR)
            sys.stdout = stdout_buf
            sys.stderr = stderr_buf
            compiled = compile(request.code, "<agent-code>", "exec")
            exec(compiled, _session_globals)  # noqa: S102
        except SystemExit as exc:
            exit_code = exc.code if isinstance(exc.code, int) else 1
        except Exception:
            stderr_buf.write(traceback.format_exc())
            exit_code = 1
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr
            os.chdir(old_cwd)

        if request.include_artifacts:
            try:
                _capture_matplotlib_figures(close_figures=request.close_figures)
                _capture_plotly_figures()
            except Exception:
                stderr_buf.write("\nArtifact capture failed:\n")
                stderr_buf.write(traceback.format_exc())
                exit_code = exit_code or 1

        after = _snapshot_workspace()
        output_files = _collect_changed_files(before, after)

    return {
        "status": "ok" if exit_code == 0 else "error",
        "stdout": stdout_buf.getvalue(),
        "stderr": stderr_buf.getvalue(),
        "exit_code": exit_code,
        "output_files": output_files,
        "artifacts": [
            item for item in output_files if _is_display_artifact(item["mime_type"])
        ],
    }


@app.post("/ci/upload")
def ci_upload(request: UploadRequest) -> dict[str, Any]:
    try:
        raw = base64.b64decode(request.content_base64, validate=True)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid base64 content: {exc}",
        ) from exc

    try:
        path = _resolve_workspace_path(request.filename)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if os.path.isdir(path):
        raise HTTPException(status_code=400, detail="Upload path is a directory.")
    if os.path.exists(path) and not request.overwrite:
        raise HTTPException(status_code=409, detail="File already exists.")

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(raw)

    return {"status": "ok", "file": _file_entry(path, include_content=False)}


@app.post("/ci/download")
def ci_download(request: PathRequest) -> dict[str, Any]:
    try:
        path = _resolve_workspace_path(request.path)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="File not found.")
    return {"status": "ok", "file": _file_entry(path, include_content=True)}


@app.post("/ci/list")
def ci_list(request: ListRequest) -> dict[str, Any]:
    try:
        path = _resolve_workspace_path(request.path)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if not os.path.isdir(path):
        raise HTTPException(status_code=404, detail="Path is not a directory.")
    return {
        "status": "ok",
        "path": _workspace_relative(path),
        "entries": _list_entries(path, recursive=request.recursive),
    }


@app.post("/ci/reset")
def ci_reset() -> dict[str, str]:
    global _artifact_counter
    with _execution_lock:
        _session_globals.clear()
        _session_globals["__name__"] = "__code_interpreter__"
        _plotly_seen_hashes.clear()
        _artifact_counter = 0
    return {"status": "ok", "message": "Python session state cleared."}


@app.post("/execute", response_model=ExecuteResponse)
async def execute_command(request: ExecuteRequest) -> ExecuteResponse:
    try:
        args = shlex.split(request.command)
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            cwd=WORKSPACE_DIR,
            timeout=request.timeout,
        )
        return ExecuteResponse(
            stdout=proc.stdout,
            stderr=proc.stderr,
            exit_code=proc.returncode,
        )
    except subprocess.TimeoutExpired as exc:
        return ExecuteResponse(
            stdout=exc.stdout.decode("utf-8", errors="replace")
            if isinstance(exc.stdout, bytes)
            else "",
            stderr=f"Command timed out after {request.timeout}s.",
            exit_code=124,
        )
    except Exception as exc:
        return ExecuteResponse(
            stdout="",
            stderr=f"Failed to execute command: {exc}",
            exit_code=1,
        )


@app.post("/upload")
async def upload_file(request: Request):
    try:
        filename, content = await _read_legacy_upload(request)
        if not filename:
            return JSONResponse(
                status_code=400, content={"message": "Missing filename"}
            )
        target_path = _resolve_workspace_path(filename)
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        with open(target_path, "wb") as handle:
            handle.write(content)
        return JSONResponse(
            status_code=200,
            content={"message": f"File '{filename}' uploaded"},
        )
    except ValueError:
        return JSONResponse(status_code=403, content={"message": "Access denied"})
    except Exception as exc:
        return JSONResponse(
            status_code=500, content={"message": f"Upload failed: {exc}"}
        )


@app.get("/download/{encoded_file_path:path}")
async def download_file(encoded_file_path: str):
    decoded_path = urllib.parse.unquote(encoded_file_path)
    try:
        full_path = _resolve_legacy_path(decoded_path)
    except ValueError:
        return JSONResponse(status_code=403, content={"message": "Access denied"})

    if os.path.isfile(full_path):
        return FileResponse(
            path=full_path,
            media_type="application/octet-stream",
            filename=os.path.basename(full_path),
        )
    return JSONResponse(status_code=404, content={"message": "File not found"})


@app.get("/list/{encoded_file_path:path}")
async def list_files(encoded_file_path: str):
    decoded_path = urllib.parse.unquote(encoded_file_path)
    try:
        full_path = _resolve_legacy_path(decoded_path)
    except ValueError:
        return JSONResponse(status_code=403, content={"message": "Access denied"})

    if not os.path.isdir(full_path):
        return JSONResponse(
            status_code=404, content={"message": "Path is not a directory"}
        )

    return JSONResponse(
        status_code=200, content=_list_entries(full_path, recursive=False)
    )


@app.get("/exists/{encoded_file_path:path}")
async def exists(encoded_file_path: str):
    decoded_path = urllib.parse.unquote(encoded_file_path)
    try:
        full_path = _resolve_legacy_path(decoded_path)
    except ValueError:
        return JSONResponse(status_code=403, content={"message": "Access denied"})

    return JSONResponse(
        status_code=200,
        content={"path": decoded_path, "exists": os.path.exists(full_path)},
    )


@app.post("/repl")
async def repl(request: RunCodeRequest):
    return JSONResponse(status_code=200, content=ci_run(request))


@app.post("/repl/reset")
async def repl_reset():
    return JSONResponse(status_code=200, content=ci_reset())


def _prepare_repl_globals() -> None:
    _session_globals.setdefault("__name__", "__code_interpreter__")
    _session_globals["display"] = _display


def _display(value: Any = None) -> Any:
    if value is None:
        return None
    if _is_plotly_figure(value):
        entries = _save_plotly_figure(value, prefix="plotly-display")
        print(f"[code_interpreter] captured plotly figure: {entries[0]['name']}")
        return entries
    if _is_matplotlib_figure(value):
        entry = _save_matplotlib_figure(value, prefix="matplotlib-display")
        print(f"[code_interpreter] captured matplotlib figure: {entry['name']}")
        return entry
    print(repr(value))
    return value


def _snapshot_workspace() -> dict[str, tuple[int, int]]:
    snapshot: dict[str, tuple[int, int]] = {}
    if not os.path.isdir(WORKSPACE_DIR):
        return snapshot

    for root, dirs, files in os.walk(WORKSPACE_DIR):
        dirs[:] = [name for name in dirs if not name.startswith(".")]
        for name in files:
            path = os.path.join(root, name)
            try:
                stat = os.stat(path)
            except OSError:
                continue
            snapshot[path] = (stat.st_mtime_ns, stat.st_size)
    return snapshot


def _collect_changed_files(
    before: dict[str, tuple[int, int]],
    after: dict[str, tuple[int, int]],
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for path in sorted(after):
        if before.get(path) == after[path]:
            continue
        results.append(_file_entry(path, include_content=True))
    return results


def _file_entry(path: str, *, include_content: bool) -> dict[str, Any]:
    size = os.path.getsize(path)
    mime_type, _ = mimetypes.guess_type(path)
    mime_type = mime_type or "application/octet-stream"
    entry: dict[str, Any] = {
        "name": _workspace_relative(path),
        "path": f"{WORKSPACE_DIR}/{_workspace_relative(path)}",
        "size": size,
        "mime_type": mime_type,
        "type": "file",
        "modified_at": os.path.getmtime(path),
    }

    if include_content:
        if size <= MAX_INLINE_FILE_BYTES:
            with open(path, "rb") as handle:
                content_base64 = base64.b64encode(handle.read()).decode("ascii")
            entry["content_base64"] = content_base64
            if _is_display_artifact(mime_type):
                entry["data_uri"] = f"data:{mime_type};base64,{content_base64}"
        else:
            entry["content_base64"] = None
            entry["too_large"] = True
    return entry


def _list_entries(path: str, *, recursive: bool) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    if recursive:
        for root, dirs, files in os.walk(path):
            dirs[:] = [name for name in dirs if not name.startswith(".")]
            for directory in dirs:
                entries.append(_directory_entry(os.path.join(root, directory)))
            for filename in files:
                entries.append(
                    _file_entry(os.path.join(root, filename), include_content=False)
                )
        return sorted(entries, key=lambda item: item["name"])

    with os.scandir(path) as iterator:
        for entry in iterator:
            if entry.name.startswith("."):
                continue
            entries.append(
                _directory_entry(entry.path)
                if entry.is_dir()
                else _file_entry(entry.path, include_content=False)
            )
    return sorted(entries, key=lambda item: item["name"])


def _directory_entry(path: str) -> dict[str, Any]:
    stat = os.stat(path)
    return {
        "name": _workspace_relative(path),
        "path": f"{WORKSPACE_DIR}/{_workspace_relative(path)}",
        "size": stat.st_size,
        "mime_type": "inode/directory",
        "type": "directory",
        "modified_at": stat.st_mtime,
    }


def _resolve_workspace_path(path: str) -> str:
    if path in {"", ".", "./", "workspace", "/app/workspace"}:
        return os.path.realpath(WORKSPACE_DIR)

    raw_path = path.strip()
    if raw_path.startswith("/app/workspace/"):
        full_path = raw_path
    elif raw_path.startswith("workspace/"):
        full_path = os.path.join(BASE_DIR, raw_path)
    elif raw_path.startswith("/"):
        raise ValueError("Path must be inside the workspace.")
    else:
        full_path = os.path.join(WORKSPACE_DIR, raw_path)

    real_workspace = os.path.realpath(WORKSPACE_DIR)
    real_path = os.path.realpath(full_path)
    if os.path.commonpath([real_workspace, real_path]) != real_workspace:
        raise ValueError("Path must be inside the workspace.")
    return real_path


def _resolve_legacy_path(path: str) -> str:
    if path.startswith("workspace/") or path.startswith("/app/workspace/"):
        return _resolve_workspace_path(path)

    clean_path = path.lstrip("/")
    full_path = os.path.realpath(os.path.join(BASE_DIR, clean_path))
    real_base = os.path.realpath(BASE_DIR)
    if os.path.commonpath([real_base, full_path]) != real_base:
        raise ValueError("Path must be inside /app.")
    return full_path


async def _read_legacy_upload(request: Request) -> tuple[str, bytes]:
    body = await request.body()
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("multipart/form-data"):
        return _parse_single_file_multipart(content_type, body)

    filename = request.headers.get("x-filename") or request.query_params.get("filename")
    if not filename:
        raise ValueError("Missing filename.")
    return filename, body


def _parse_single_file_multipart(content_type: str, body: bytes) -> tuple[str, bytes]:
    boundary_marker = "boundary="
    if boundary_marker not in content_type:
        raise ValueError("Missing multipart boundary.")
    boundary = (
        content_type.split(boundary_marker, 1)[1].split(";", 1)[0].strip().strip('"')
    )
    delimiter = f"--{boundary}".encode("utf-8")

    for raw_part in body.split(delimiter):
        part = raw_part.strip(b"\r\n")
        if not part or part == b"--":
            continue
        headers, separator, content = part.partition(b"\r\n\r\n")
        if not separator:
            continue
        filename = _multipart_filename(headers.decode("utf-8", errors="replace"))
        if filename is None:
            continue
        if content.endswith(b"\r\n"):
            content = content[:-2]
        if content.endswith(b"--"):
            content = content[:-2]
        return filename, content

    raise ValueError("No file part found in multipart upload.")


def _multipart_filename(headers: str) -> str | None:
    for header in headers.splitlines():
        if not header.lower().startswith("content-disposition:"):
            continue
        for item in header.split(";"):
            key, separator, value = item.strip().partition("=")
            if separator and key == "filename":
                return value.strip().strip('"')
    return None


def _workspace_relative(path: str) -> str:
    rel = os.path.relpath(os.path.realpath(path), os.path.realpath(WORKSPACE_DIR))
    return "" if rel == "." else rel


def _new_artifact_path(prefix: str, extension: str) -> str:
    global _artifact_counter
    _artifact_counter += 1
    os.makedirs(ARTIFACTS_DIR, exist_ok=True)
    stamp = int(time.time() * 1000)
    suffix = uuid.uuid4().hex[:8]
    return os.path.join(
        ARTIFACTS_DIR,
        f"{prefix}-{stamp}-{_artifact_counter}-{suffix}.{extension}",
    )


def _capture_matplotlib_figures(*, close_figures: bool) -> list[dict[str, Any]]:
    try:
        import matplotlib.pyplot as plt
    except Exception:
        return []

    entries: list[dict[str, Any]] = []
    for number in list(plt.get_fignums()):
        figure = plt.figure(number)
        entries.append(_save_matplotlib_figure(figure, prefix="matplotlib"))
        if close_figures:
            plt.close(figure)
    return entries


def _save_matplotlib_figure(figure: Any, *, prefix: str) -> dict[str, Any]:
    path = _new_artifact_path(prefix, "png")
    figure.savefig(path, bbox_inches="tight", dpi=144)
    return _file_entry(path, include_content=True)


def _is_matplotlib_figure(value: Any) -> bool:
    return (
        value.__class__.__name__ == "Figure"
        and value.__class__.__module__.startswith("matplotlib")
    )


def _install_plotly_show_hook() -> None:
    try:
        from plotly.basedatatypes import BaseFigure
    except Exception:
        return

    current_show = getattr(BaseFigure, "show", None)
    if getattr(current_show, "_code_interpreter_patched", False):
        return

    def _show(self: Any, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        entries = _save_plotly_figure(self, prefix="plotly-show")
        print(f"[code_interpreter] captured plotly figure: {entries[0]['name']}")
        return entries

    _show._code_interpreter_patched = True  # type: ignore[attr-defined]
    BaseFigure.show = _show  # type: ignore[assignment]


def _capture_plotly_figures() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for name, value in list(_session_globals.items()):
        if _is_plotly_figure(value):
            digest = _plotly_hash(value)
            if _plotly_seen_hashes.get(name) == digest:
                continue
            entries.extend(_save_plotly_figure(value, prefix="plotly"))
            _plotly_seen_hashes[name] = digest
    return entries


def _save_plotly_figure(figure: Any, *, prefix: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    html_path = _new_artifact_path(prefix, "html")
    figure.write_html(html_path, include_plotlyjs="cdn", full_html=True)
    entries.append(_file_entry(html_path, include_content=True))

    try:
        png_path = _new_artifact_path(prefix, "png")
        figure.write_image(png_path, scale=2)
        entries.append(_file_entry(png_path, include_content=True))
    except Exception:
        pass

    # For figures captured via the show() hook, use id() as the key (no variable name available).
    _plotly_seen_hashes[str(id(figure))] = _plotly_hash(figure)
    return entries


def _is_plotly_figure(value: Any) -> bool:
    return hasattr(value, "write_html") and value.__class__.__module__.startswith(
        "plotly"
    )


def _plotly_hash(figure: Any) -> str:
    try:
        payload = figure.to_json()
    except Exception:
        payload = repr(figure)
    return hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()


def _is_display_artifact(mime_type: str) -> bool:
    return mime_type.startswith("image/") or mime_type in {
        "text/html",
        "application/pdf",
        "image/svg+xml",
    }
