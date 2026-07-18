"""Small newline-delimited JSON-RPC server used by mcpclient tests."""

import json
import os
import sys
import time


def send(message: dict) -> None:
    print(json.dumps(message, separators=(",", ":")), flush=True)


def serve() -> None:
    for line in sys.stdin:
        message = json.loads(line)
        if message.get("method") == "initialize":
            send(
                {
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "result": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "fake", "version": "1.0"},
                        "instructions": "Fake server instructions.",
                    },
                }
            )
        elif message.get("method") == "tools/list":
            cursor = message.get("params", {}).get("cursor")
            if cursor is None:
                send(
                    {
                        "jsonrpc": "2.0",
                        "id": message["id"],
                        "result": {
                            "tools": [
                                {
                                    "name": "first_tool",
                                    "description": "The first fake tool",
                                    "inputSchema": {
                                        "type": "object",
                                        "properties": {},
                                    },
                                }
                            ],
                            "nextCursor": "second-page",
                        },
                    }
                )
            else:
                send(
                    {
                        "jsonrpc": "2.0",
                        "id": message["id"],
                        "result": {
                            "tools": [
                                {
                                    "name": "second_tool",
                                    "description": "The second fake tool",
                                    "inputSchema": {
                                        "type": "object",
                                        "properties": {
                                            "value": {"type": "string"}
                                        },
                                    },
                                }
                            ]
                        },
                    }
                )


def serve_error(sentinel: str) -> None:
    """Reply to ``initialize`` with a JSON-RPC error carrying a sentinel.

    Used to prove a malicious or buggy server cannot smuggle secrets into
    any mcp-top output mode through ``error.message``/``error.data``.
    """

    for line in sys.stdin:
        message = json.loads(line)
        if message.get("method") == "initialize":
            send(
                {
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "error": {
                        "code": -32000,
                        "message": f"boom: {sentinel}",
                        "data": {"secret": sentinel},
                    },
                }
            )
            return


def sleep_forever(pid_path: str) -> None:
    with open(pid_path, "w", encoding="utf-8") as handle:
        handle.write(str(os.getpid()))
    time.sleep(60)


def sleep_with_grandchild(pid_path: str, grandchild_pid_path: str) -> None:
    """Spawn a grandchild that inherits stdio, then hang.

    Used to prove cleanup kills the whole process tree, not just the
    direct child.
    """

    import subprocess

    child = subprocess.Popen(
        [sys.executable, os.path.abspath(__file__), "sleep", grandchild_pid_path]
    )
    with open(pid_path, "w", encoding="utf-8") as handle:
        handle.write(str(os.getpid()))
    child.wait()


if __name__ == "__main__":
    if sys.argv[1] == "serve":
        serve()
    elif sys.argv[1] == "serve-error":
        serve_error(sys.argv[2])
    elif sys.argv[1] == "sleep-with-grandchild":
        sleep_with_grandchild(sys.argv[2], sys.argv[3])
    else:
        sleep_forever(sys.argv[2])
