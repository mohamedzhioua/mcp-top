"""Minimal read-only MCP client for listing stdio server tools."""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any, TextIO

from mcp_top.config import ServerConfig


@dataclass
class ServerTools:
    """Tool definitions returned by one configured MCP server."""

    server: str
    status: str
    error: str | None
    tools: list[dict]


def list_server_tools(cfg: ServerConfig, timeout: float = 20.0) -> ServerTools:
    """Query one stdio server for all tool definitions without leaking errors."""

    if cfg.transport != "stdio":
        return ServerTools(
            server=cfg.name,
            status="unsupported",
            error="unsupported transport (v0.1 queries stdio only)",
            tools=[],
        )
    if cfg.command is None:
        return ServerTools(
            server=cfg.name,
            status="error",
            error="stdio server has no command",
            tools=[],
        )

    process: subprocess.Popen[str] | None = None
    reader: threading.Thread | None = None
    messages: queue.Queue[tuple[str, Any]] = queue.Queue()
    deadline = time.monotonic() + timeout
    try:
        command = cfg.command
        if os.name == "nt":
            command = shutil.which(command) or command
        process = subprocess.Popen(
            [command, *cfg.args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env={**os.environ, **cfg.env},
            text=True,
            encoding="utf-8",
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
                    "clientInfo": {"name": "mcp-top", "version": "0.1.0"},
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

        return ServerTools(
            server=cfg.name,
            status="ok",
            error=None,
            tools=tools,
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


def _send(stream: TextIO, message: dict[str, Any]) -> None:
    stream.write(json.dumps(message, separators=(",", ":")) + "\n")
    stream.flush()


def _read_stdout(
    stream: TextIO, messages: queue.Queue[tuple[str, Any]]
) -> None:
    try:
        for line in stream:
            messages.put(("line", line))
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


def _stop_process(process: subprocess.Popen[str]) -> None:
    try:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
    except Exception:
        try:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=2)
        except Exception:
            pass
    finally:
        for stream in (process.stdin, process.stdout):
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass
