"""Tests for the import-safe Python runtime floor check."""

from __future__ import annotations

import unittest

import _path  # noqa: F401

from mcp_top._version_gate import check_python_version


class VersionGateTests(unittest.TestCase):
    def test_python_version_floor_logic_is_importable_without_cli(self) -> None:
        self.assertFalse(check_python_version((3, 10, 13)))
        self.assertTrue(check_python_version((3, 11, 0)))


if __name__ == "__main__":
    unittest.main()
