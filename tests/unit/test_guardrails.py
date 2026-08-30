import asyncio
import unittest
from types import SimpleNamespace

from app.guardrails import read_only_tool_guard


class ReadOnlyToolGuardTests(unittest.TestCase):
    def test_allows_registered_read_only_tool(self) -> None:
        result = asyncio.run(
            read_only_tool_guard(
                tool=SimpleNamespace(name="inspect_github_repository"),
                args={"repository": "noktohq/repo"},
                tool_context=SimpleNamespace(),
            )
        )

        self.assertIsNone(result)

    def test_blocks_unregistered_tool(self) -> None:
        result = asyncio.run(
            read_only_tool_guard(
                tool=SimpleNamespace(name="delete_repository"),
                args={},
                tool_context=SimpleNamespace(),
            )
        )

        self.assertIsNotNone(result)
        self.assertTrue(result["permission_denied"])

    def test_blocks_oversized_arguments(self) -> None:
        result = asyncio.run(
            read_only_tool_guard(
                tool=SimpleNamespace(name="analyze_change_risk"),
                args={"task": "x" * 40_000},
                tool_context=SimpleNamespace(),
            )
        )

        self.assertIsNotNone(result)
        self.assertEqual(result["error_code"], "tool_arguments_too_large")


if __name__ == "__main__":
    unittest.main()
