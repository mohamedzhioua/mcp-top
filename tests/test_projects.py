"""Unit tests for project identity helpers."""

from __future__ import annotations

import unittest

import _path  # noqa: F401

from mcp_top.projects import claude_project_slug, normalize_project_key


class ProjectHelperTests(unittest.TestCase):
    def test_claude_project_slug_matches_verified_windows_ground_truth(self) -> None:
        self.assertEqual(
            claude_project_slug(r"C:\Users\User\Desktop\your-ai-engineering-os"),
            "C--Users-User-Desktop-your-ai-engineering-os",
        )

    def test_normalize_project_key_is_idempotent(self) -> None:
        key = normalize_project_key(r"C:\Users\User\Desktop\proj")

        self.assertEqual(normalize_project_key(key), key)

    def test_normalize_project_key_treats_slashes_equivalently(self) -> None:
        self.assertEqual(
            normalize_project_key(r"C:\Users\User\Desktop\proj"),
            normalize_project_key("C:/Users/User/Desktop/proj"),
        )

    def test_normalize_project_key_strips_trailing_slash(self) -> None:
        self.assertEqual(
            normalize_project_key("/tmp/project/"),
            normalize_project_key("/tmp/project"),
        )

    def test_claude_project_slug_is_lossy_and_can_collide(self) -> None:
        self.assertNotEqual("/tmp/a_b", "/tmp/a.b")
        self.assertEqual(
            claude_project_slug("/tmp/a_b"),
            claude_project_slug("/tmp/a.b"),
        )


if __name__ == "__main__":
    unittest.main()
