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


def sleep_forever(pid_path: str) -> None:
    with open(pid_path, "w", encoding="utf-8") as handle:
        handle.write(str(os.getpid()))
    time.sleep(60)


if __name__ == "__main__":
    if sys.argv[1] == "serve":
        serve()
    else:
        sleep_forever(sys.argv[2])
