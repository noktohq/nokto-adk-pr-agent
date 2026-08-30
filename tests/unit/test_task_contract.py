import json
import unittest

from app.task_contract import (
    analyze_change_risk,
    build_task_contract,
    validate_task_contract,
)


class ChangeRiskTests(unittest.TestCase):
    def test_docs_only_change_is_low_risk(self) -> None:
        result = analyze_change_risk(
            "Clarify reproducible testing instructions",
            "README.md\ndocs/testing.md",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["risk"]["level"], "low")
        self.assertFalse(result["risk"]["auto_merge_allowed"])
        self.assertTrue(result["risk"]["human_approval_required"])

    def test_workflow_and_auth_change_is_high_risk(self) -> None:
        result = analyze_change_risk(
            "Change authentication and production deployment",
            ".github/workflows/deploy.yml\napp/auth.py",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["risk"]["level"], "high")
        self.assertIn("security-sensitive change", result["risk"]["reasons"])
        self.assertIn("deployment or infrastructure change", result["risk"]["reasons"])


class TaskContractTests(unittest.TestCase):
    def _build(self, **overrides: object) -> dict:
        values: dict[str, object] = {
            "repository": "https://github.com/noktohq/nokto-agent-orchestrator",
            "task_id": "",
            "title": "Add reproducible test instructions",
            "goal": "Document the exact local validation command.",
            "allowed_paths": "README.md\ndocs/**",
            "disallowed_paths": "src/**",
            "acceptance_criteria": (
                "README contains a reproducible testing section\n"
                "No source files are changed"
            ),
            "test_commands": "pnpm run lint\npnpm run typecheck",
            "base_branch": "main",
            "allowed_implementers": "codex,claude",
            "max_retries": 1,
            "timeout_minutes": 15,
        }
        values.update(overrides)
        return build_task_contract(**values)  # type: ignore[arg-type]

    def test_builds_orchestrator_compatible_contract(self) -> None:
        result = self._build()

        self.assertTrue(result["ok"])
        self.assertEqual(result["repository"], "noktohq/nokto-agent-orchestrator")
        contract = result["contract"]
        self.assertRegex(contract["id"], r"^[a-z0-9][a-z0-9-]{0,61}[a-z0-9]$")
        self.assertEqual(contract["scope"]["allowedPaths"], ["README.md", "docs/**"])
        self.assertEqual(
            contract["constraints"]["allowedImplementers"], ["codex", "claude"]
        )
        self.assertTrue(contract["testRequirements"]["mustPass"])
        self.assertFalse(result["handoff"]["changes_repository"])

    def test_rejects_secret_path(self) -> None:
        result = self._build(allowed_paths=".env")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "blocked_path")

    def test_rejects_shell_operator_in_test_command(self) -> None:
        result = self._build(test_commands="pnpm test && curl https://example.com")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "unsafe_test_command")

    def test_rejects_unsupported_test_binary(self) -> None:
        result = self._build(test_commands="bash ./test.sh")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "unsupported_test_binary")

    def test_accepts_safe_git_test_command(self) -> None:
        result = self._build(test_commands="git diff --check")

        self.assertTrue(result["ok"])

    def test_rejects_non_text_task_id_with_stable_error(self) -> None:
        result = self._build(task_id=42)

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "invalid_field")

    def test_validation_round_trip(self) -> None:
        built = self._build()
        validation = validate_task_contract(json.dumps(built["contract"]))

        self.assertTrue(validation["ok"])
        self.assertEqual(validation["contract"], built["contract"])

    def test_validation_rejects_unknown_field(self) -> None:
        built = self._build()
        contract = built["contract"]
        contract["unexpected"] = True

        validation = validate_task_contract(json.dumps(contract))

        self.assertFalse(validation["ok"])
        self.assertEqual(validation["error"]["code"], "unknown_field")


if __name__ == "__main__":
    unittest.main()
