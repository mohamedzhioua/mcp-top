"""Integration tests for the MCP newline-delimited stdio client."""

from __future__ import annotations

import ctypes
import os
import sys
import tempfile
import time
import unittest

import _path  # noqa: F401

from mcp_top.config import ServerConfig
from mcp_top.engine import build_cli_report
from mcp_top.mcpclient import list_server_tools


FAKE_SERVER = os.path.join(os.path.dirname(__file__), "fixtures", "fake_mcp_server.py")


class McpClientTests(unittest.TestCase):
    """Exercises successful, unsupported, missing, and timed-out servers."""

    def test_lists_tools_across_paginated_responses(self) -> None:
        config = self._config(sys.executable, [FAKE_SERVER, "serve"])

        result = list_server_tools(config, timeout=5)

        self.assertEqual(result.server, "fake")
        self.assertEqual(result.status, "ok")
        self.assertIsNone(result.error)
        self.assertEqual(
            [tool["name"] for tool in result.tools], ["first_tool", "second_tool"]
        )

    def test_http_transport_is_explicitly_unsupported(self) -> None:
        config = self._config(None, [], transport="http")

        result = list_server_tools(config)

        self.assertEqual(result.status, "unsupported")
        self.assertEqual(
            result.error,
            "unsupported transport (this version queries stdio only)",
        )
        self.assertEqual(result.tools, [])

    def test_disabled_server_is_not_launched(self) -> None:
        config = self._config(
            "mcp-top-command-that-does-not-exist",
            [],
            enabled=False,
        )

        result = list_server_tools(config)

        self.assertEqual(result.status, "unsupported")
        self.assertEqual(result.error, "disabled in config -- not queried")
        self.assertEqual(result.tools, [])

    def test_missing_command_returns_error(self) -> None:
        config = self._config("mcp-top-command-that-does-not-exist", [])

        result = list_server_tools(config, timeout=2)

        self.assertEqual(result.status, "error")
        self.assertIsNotNone(result.error)
        self.assertEqual(result.tools, [])

    def test_nonexistent_cwd_returns_error_without_launching(self) -> None:
        config = self._config(
            sys.executable,
            [FAKE_SERVER, "serve"],
            cwd=os.path.join(tempfile.gettempdir(), "mcp-top-missing-cwd"),
        )

        result = list_server_tools(config)

        self.assertEqual(result.status, "error")
        self.assertIn("cwd does not exist:", result.error or "")
        self.assertEqual(result.tools, [])

    def test_tool_filters_hide_tools_before_definition_tokens(self) -> None:
        config = self._config(
            sys.executable,
            [FAKE_SERVER, "serve"],
            enabled_tools=["first_tool"],
        )

        result = list_server_tools(config, timeout=5)
        report = build_cli_report("test", [config], [result], [], None)

        self.assertEqual(result.status, "ok")
        self.assertEqual([tool["name"] for tool in result.tools], ["first_tool"])
        self.assertEqual(result.filtered_tools, 1)
        row = report.rows[0]
        self.assertEqual(row.tool_count, 1)
        self.assertEqual(row.filtered_tools, 1)
        self.assertIsNotNone(row.def_tokens)

    def test_unmatched_enabled_tools_are_reported(self) -> None:
        config = self._config(
            sys.executable,
            [FAKE_SERVER, "serve"],
            enabled_tools=["first_tool", "typo_tool", "gone_tool"],
        )

        result = list_server_tools(config, timeout=5)

        self.assertEqual(result.status, "ok")
        # first_tool is live; the other two names match no returned tool.
        self.assertEqual(
            result.unmatched_enabled_tools, ["gone_tool", "typo_tool"]
        )

    def test_no_allowlist_reports_no_unmatched_tools(self) -> None:
        config = self._config(sys.executable, [FAKE_SERVER, "serve"])

        result = list_server_tools(config, timeout=5)

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.unmatched_enabled_tools, [])

    def test_timeout_returns_error_and_stops_child(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pid_path = os.path.join(directory, "server.pid")
            config = self._config(
                sys.executable, [FAKE_SERVER, "sleep", pid_path]
            )

            result = list_server_tools(config, timeout=2)

            self.assertEqual(result.status, "error")
            self.assertIn("timeout", result.error.lower())
            with open(pid_path, "r", encoding="utf-8") as handle:
                pid = int(handle.read())
            self.assertFalse(self._process_exists(pid))

    def test_timeout_stops_grandchild_process_tree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pid_path = os.path.join(directory, "server.pid")
            grandchild_pid_path = os.path.join(directory, "grandchild.pid")
            config = self._config(
                sys.executable,
                [
                    FAKE_SERVER,
                    "sleep-with-grandchild",
                    pid_path,
                    grandchild_pid_path,
                ],
            )

            result = list_server_tools(config, timeout=3)

            self.assertEqual(result.status, "error")
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if os.path.exists(grandchild_pid_path):
                    break
                time.sleep(0.1)
            with open(grandchild_pid_path, "r", encoding="utf-8") as handle:
                grandchild_pid = int(handle.read())
            # Reaping the whole tree may take a moment after the kill.
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if not self._process_exists(grandchild_pid):
                    break
                time.sleep(0.2)
            self.assertFalse(self._process_exists(grandchild_pid))

    def _config(
        self,
        command: str | None,
        args: list[str],
        transport: str = "stdio",
        enabled: bool = True,
        enabled_tools: list[str] | None = None,
        cwd: str | None = None,
    ) -> ServerConfig:
        return ServerConfig(
            name="fake",
            scope="user",
            source_path="test",
            transport=transport,
            command=command,
            args=args,
            env={},
            url="https://example.com/mcp" if transport != "stdio" else None,
            enabled=enabled,
            enabled_tools=enabled_tools,
            cwd=cwd,
        )

    def _process_exists(self, pid: int) -> bool:
        if os.name == "nt":
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = [
                wintypes.DWORD,
                wintypes.BOOL,
                wintypes.DWORD,
            ]
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel32.WaitForSingleObject.restype = wintypes.DWORD
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            synchronize = 0x00100000
            handle = kernel32.OpenProcess(synchronize, False, pid)
            if not handle:
                return False
            try:
                wait_timeout = 0x00000102
                return kernel32.WaitForSingleObject(handle, 0) == wait_timeout
            finally:
                kernel32.CloseHandle(handle)
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True


if __name__ == "__main__":
    unittest.main()
