"""Minimal read-only MCP client for listing stdio server tools."""

from __future__ import annotations

import json
import os
import queue
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import IO, Any

from mcp_top import __version__
from mcp_top.config import ServerConfig


@dataclass
class ServerTools:
    """Tool definitions returned by one configured MCP server.

    ``unmatched_enabled_tools`` lists ``enabled_tools`` allowlist names that
    were not returned by this ``tools/list`` snapshot -- likely typos or
    removed tools. It is only meaningful when ``status == "ok"`` (the tool set
    was fully observed) and describes this snapshot, not all future live tools.
    """

    server: str
    status: str
    error: str | None
    tools: list[dict]
    filtered_tools: int = 0
    unmatched_enabled_tools: list[str] = field(default_factory=list)


def list_server_tools(cfg: ServerConfig, timeout: float = 20.0) -> ServerTools:
    """Query one stdio server for all tool definitions without leaking errors."""

    if cfg.enabled is False:
        return ServerTools(
            server=cfg.name,
            status="unsupported",
            error="disabled in config -- not queried",
            tools=[],
        )
    if cfg.transport != "stdio":
        return ServerTools(
            server=cfg.name,
            status="unsupported",
            error="unsupported transport (this version queries stdio only)",
            tools=[],
        )
    if cfg.command is None:
        return ServerTools(
            server=cfg.name,
            status="error",
            error="stdio server has no command",
            tools=[],
        )
    if cfg.cwd is not None and not os.path.isdir(cfg.cwd):
        return ServerTools(
            server=cfg.name,
            status="error",
            error=f"cwd does not exist: {cfg.cwd}",
            tools=[],
        )

    process: subprocess.Popen[bytes] | None = None
    reader: threading.Thread | None = None
    messages: queue.Queue[tuple[str, Any]] = queue.Queue()
    deadline = time.monotonic() + timeout
    try:
        command = cfg.command
        if os.name == "nt":
            command = shutil.which(command) or command
        popen_kwargs: dict[str, Any] = {}
        if os.name == "nt":
            # Let CTRL_BREAK stop the whole tree when taskkill is unavailable.
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            # Own process group so cleanup can kill the whole server tree.
            popen_kwargs["start_new_session"] = True
        process = subprocess.Popen(
            [command, *cfg.args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env={**os.environ, **cfg.env},
            cwd=cfg.cwd,
            **popen_kwargs,
        )
        if process.stdin is None or process.stdout is None:
            raise RuntimeError("could not open server stdio pipes")

        reader = threading.Thread(
            target=_read_stdout,
            args=(process.stdout, messages),
            daemon=True,
        )
        reader.start()

        _send(
            process.stdin,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "mcp-top", "version": __version__},
                },
            },
        )
        _wait_for_response(messages, 1, deadline, timeout)
        _send(
            process.stdin,
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
        )

        tools: list[dict] = []
        request_id = 2
        params: dict[str, str] = {}
        while True:
            _send(
                process.stdin,
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "tools/list",
                    "params": params,
                },
            )
            response = _wait_for_response(
                messages, request_id, deadline, timeout
            )
            result = response.get("result")
            if not isinstance(result, dict):
                raise RuntimeError("tools/list response has no result object")
            page_tools = result.get("tools")
            if not isinstance(page_tools, list):
                raise RuntimeError("tools/list response has no tools list")
            tools.extend(tool for tool in page_tools if isinstance(tool, dict))

            cursor = result.get("nextCursor")
            if cursor is None or cursor == "":
                break
            if not isinstance(cursor, str):
                raise RuntimeError("tools/list nextCursor is not a string")
            request_id += 1
            params = {"cursor": cursor}

        unmatched = _unmatched_enabled_tools(tools, cfg)
        filtered_tools, hidden = _apply_tool_filters(tools, cfg)

        return ServerTools(
            server=cfg.name,
            status="ok",
            error=None,
            tools=filtered_tools,
            filtered_tools=hidden,
            unmatched_enabled_tools=unmatched,
        )
    except TimeoutError as err:
        return ServerTools(
            server=cfg.name,
            status="error",
            error=str(err),
            tools=[],
        )
    except Exception as err:
        return ServerTools(
            server=cfg.name,
            status="error",
            error=str(err) or err.__class__.__name__,
            tools=[],
        )
    finally:
        if process is not None:
            _stop_process(process)
        if reader is not None:
            try:
                reader.join(timeout=2)
            except RuntimeError:
                pass
        if process is not None and (reader is None or not reader.is_alive()):
            # Safe only once no thread can be blocked on the stream.
            if process.stdout is not None:
                try:
                    process.stdout.close()
                except Exception:
                    pass


def _send(stream: IO[bytes], message: dict[str, Any]) -> None:
    # Binary pipes keep JSON-RPC frames LF-terminated on every platform;
    # text mode would rewrite "\n" to os.linesep on Windows.
    stream.write(
        (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")
    )
    stream.flush()


def _unmatched_enabled_tools(
    tools: list[dict], cfg: ServerConfig
) -> list[str]:
    """Return sorted ``enabled_tools`` names not present in this tool snapshot.

    Uses the raw returned tool names before any allow/deny filtering. A name
    that also appears in ``disabled_tools`` still counts as matched: it was
    returned by the server, just filtered out afterward.
    """

    if not cfg.enabled_tools:
        return []
    live_names = {
        tool["name"]
        for tool in tools
        if isinstance(tool.get("name"), str)
    }
    return sorted(set(cfg.enabled_tools) - live_names)


def _apply_tool_filters(
    tools: list[dict], cfg: ServerConfig
) -> tuple[list[dict], int]:
    original_count = len(tools)
    filtered = tools
    if cfg.enabled_tools is not None:
        enabled = set(cfg.enabled_tools)
        filtered = [
            tool for tool in filtered if isinstance(tool.get("name"), str)
            and tool.get("name") in enabled
        ]
    if cfg.disabled_tools is not None:
        disabled = set(cfg.disabled_tools)
        filtered = [
            tool for tool in filtered if not (
                isinstance(tool.get("name"), str)
                and tool.get("name") in disabled
            )
        ]
    return filtered, original_count - len(filtered)


def _read_stdout(
    stream: IO[bytes], messages: queue.Queue[tuple[str, Any]]
) -> None:
    try:
        for raw in stream:
            messages.put(("line", raw.decode("utf-8", "replace")))
    except Exception as err:
        messages.put(("error", err))
    finally:
        messages.put(("eof", None))


def _wait_for_response(
    messages: queue.Queue[tuple[str, Any]],
    response_id: int,
    deadline: float,
    timeout: float,
) -> dict[str, Any]:
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"timeout after {timeout:g} seconds")
        try:
            kind, payload = messages.get(timeout=remaining)
        except queue.Empty:
            raise TimeoutError(f"timeout after {timeout:g} seconds") from None
        if kind == "error":
            raise RuntimeError(f"could not read server output: {payload}")
        if kind == "eof":
            raise RuntimeError(
                f"server closed stdout before response id {response_id}"
            )
        try:
            message = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if not isinstance(message, dict) or message.get("id") != response_id:
            continue
        if "method" in message or not ({"result", "error"} & message.keys()):
            continue
        if "error" in message:
            error = json.dumps(message["error"], ensure_ascii=False)
            raise RuntimeError(f"server returned JSON-RPC error: {error}")
        return message


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    """Stop the server and its whole process tree.

    Wrapper commands (npx.cmd, launcher scripts) spawn grandchildren that
    inherit the stdout pipe; terminating only the direct child leaves that
    pipe open, so the reader thread would block forever. Kill the tree.
    Never close process.stdout here: closing a buffered stream while the
    reader thread is blocked on it deadlocks on the stream's internal lock.
    """

    try:
        if process.poll() is None:
            if os.name == "nt":
                try:
                    process.send_signal(signal.CTRL_BREAK_EVENT)
                    process.wait(timeout=1)
                except (OSError, subprocess.TimeoutExpired):
                    pass
                if process.poll() is None:
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=5,
                    )
            else:
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                except (OSError, PermissionError):
                    process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                _kill_hard(process)
                process.wait(timeout=2)
    except Exception:
        try:
            if process.poll() is None:
                _kill_hard(process)
                process.wait(timeout=2)
        except Exception:
            pass
    finally:
        if process.stdin is not None:
            try:
                process.stdin.close()
            except Exception:
                pass


def _kill_hard(process: subprocess.Popen[bytes]) -> None:
    """Last-resort kill that still targets the whole tree where possible."""

    if os.name != "nt":
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            return
        except (OSError, PermissionError):
            pass
    process.kill()
