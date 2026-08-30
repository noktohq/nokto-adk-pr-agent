from __future__ import annotations

import hashlib
import json
import re
import shlex
import unicodedata
from typing import Any

from app.github_tools import normalize_repository

_TASK_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,61}[a-z0-9]$")
_BRANCH = re.compile(r"^[A-Za-z0-9._/-]{1,255}$")
_SAFE_TEST_BINARIES = {
    "eslint",
    "git",
    "node",
    "npm",
    "pnpm",
    "prettier",
    "pytest",
    "python3",
    "tsc",
    "vitest",
}
_DEFAULT_DISALLOWED_PATHS = [
    ".git/**",
    ".env*",
    "**/*.key",
    "**/*.p12",
    "**/*.pem",
    "**/*.pfx",
    "**/secrets/**",
]
_ALLOWED_IMPLEMENTERS = {"claude", "codex"}
_MAX_CONTRACT_BYTES = 65_536
_MAX_LIST_ITEMS = 50


class ContractValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _failure(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message}}


def _required_text(value: Any, field: str, *, maximum: int = 4_000) -> str:
    if not isinstance(value, str):
        raise ContractValidationError("invalid_field", f"{field} must be a string.")
    normalized = value.strip()
    if not normalized:
        raise ContractValidationError("missing_field", f"{field} is required.")
    if len(normalized) > maximum:
        raise ContractValidationError(
            "field_too_large", f"{field} exceeds {maximum} characters."
        )
    return normalized


def _parse_lines(value: str, field: str) -> list[str]:
    if not isinstance(value, str):
        raise ContractValidationError("invalid_field", f"{field} must be text.")
    items = [line.strip() for line in value.splitlines() if line.strip()]
    if not items:
        raise ContractValidationError("missing_field", f"{field} requires one item.")
    if len(items) > _MAX_LIST_ITEMS:
        raise ContractValidationError(
            "too_many_items", f"{field} supports at most {_MAX_LIST_ITEMS} items."
        )
    return list(dict.fromkeys(items))


def _validate_path(path: str, *, allowed: bool) -> str:
    value = _required_text(path, "path", maximum=300)
    if (
        value.startswith("/")
        or "\\" in value
        or "\x00" in value
        or any(part == ".." for part in value.split("/"))
    ):
        raise ContractValidationError(
            "invalid_path", f"Path '{value}' must be repository-relative."
        )
    if allowed:
        parts = {part.lower() for part in value.split("/")}
        basename = value.rsplit("/", 1)[-1].lower()
        if (
            ".git" in parts
            or "secrets" in parts
            or basename == ".env"
            or basename.startswith(".env.")
            or basename.endswith((".key", ".p12", ".pem", ".pfx"))
        ):
            raise ContractValidationError(
                "blocked_path", f"Sensitive path '{value}' cannot be allowed."
            )
    return value


def _validate_branch(
    value: Any, field: str, *, allow_trailing_slash: bool = False
) -> str:
    branch = _required_text(value, field, maximum=255)
    if (
        not _BRANCH.fullmatch(branch)
        or ".." in branch
        or "@{" in branch
        or branch.startswith("/")
        or (branch.endswith("/") and not allow_trailing_slash)
    ):
        raise ContractValidationError("invalid_branch", f"{field} is invalid.")
    return branch


def _validate_test_command(command: str) -> str:
    value = _required_text(command, "test command", maximum=300)
    if re.search(r"[;&|<>`$()\n\r]", value):
        raise ContractValidationError(
            "unsafe_test_command",
            f"Test command '{value}' contains a blocked shell operator.",
        )
    try:
        arguments = shlex.split(value, posix=True)
    except ValueError as exc:
        raise ContractValidationError(
            "unsafe_test_command", f"Test command '{value}' cannot be parsed."
        ) from exc
    if not arguments or arguments[0] not in _SAFE_TEST_BINARIES:
        raise ContractValidationError(
            "unsupported_test_binary",
            f"Test command must start with: {', '.join(sorted(_SAFE_TEST_BINARIES))}.",
        )
    return value


def _validate_task_id(value: Any) -> str:
    task_id = _required_text(value, "id", maximum=63)
    if not _TASK_ID.fullmatch(task_id):
        raise ContractValidationError(
            "invalid_task_id", "id must be lowercase kebab-case with 2-63 characters."
        )
    return task_id


def _integer(value: Any, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractValidationError("invalid_field", f"{field} must be an integer.")
    if not minimum <= value <= maximum:
        raise ContractValidationError(
            "invalid_field", f"{field} must be between {minimum} and {maximum}."
        )
    return value


def _string_list(value: Any, field: str, *, minimum: int = 1) -> list[str]:
    if not isinstance(value, list) or len(value) < minimum:
        raise ContractValidationError(
            "invalid_field", f"{field} must contain at least {minimum} item(s)."
        )
    if len(value) > _MAX_LIST_ITEMS:
        raise ContractValidationError(
            "too_many_items", f"{field} supports at most {_MAX_LIST_ITEMS} items."
        )
    return [_required_text(item, field) for item in value]


def _exact_fields(
    value: Any, field: str, *, allowed: set[str], required: set[str]
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractValidationError("invalid_field", f"{field} must be an object.")
    unknown = set(value) - allowed
    if unknown:
        raise ContractValidationError(
            "unknown_field",
            f"{field} contains unknown field(s): {', '.join(sorted(unknown))}.",
        )
    missing = required - set(value)
    if missing:
        raise ContractValidationError(
            "missing_field", f"{field} is missing: {', '.join(sorted(missing))}."
        )
    return value


def _generated_task_id(repository: str, title: str, goal: str) -> str:
    ascii_title = (
        unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii")
    )
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_title.lower()).strip("-") or "change"
    digest = hashlib.sha256(f"{repository}\x00{title}\x00{goal}".encode()).hexdigest()[
        :8
    ]
    return f"{slug[:48].rstrip('-')}-{digest}"


def analyze_change_risk(task: str, changed_files: str) -> dict[str, Any]:
    """Calculates deterministic delivery risk for a requested code change.

    Args:
        task: Plain-language description of the requested change.
        changed_files: Newline-separated expected file paths or glob patterns.

    Returns:
        A JSON-compatible risk report with mandatory controls.
    """
    try:
        normalized_task = _required_text(task, "task")
        files = _parse_lines(changed_files, "changed_files")
        lowered_files = [path.lower() for path in files]
        signal_text = f"{normalized_task.lower()} {' '.join(lowered_files)}"
        docs_only = all(
            path.startswith("docs/")
            or path.startswith("readme")
            or path.endswith((".md", ".txt"))
            for path in lowered_files
        )
        score = 1 if docs_only else 3
        reasons: list[str] = []
        controls = [
            "isolated worktree",
            "secret scan",
            "independent code review",
            "human merge approval",
        ]

        signals = [
            (
                4,
                "security-sensitive change",
                ("auth", "security", "permission", "iam", "oauth"),
                "security-focused tests",
            ),
            (
                4,
                "payment or billing change",
                ("payment", "billing", "invoice", "checkout", "vipps", "stripe"),
                "payment failure-path tests",
            ),
            (
                4,
                "deployment or infrastructure change",
                (".github/workflows", "deploy", "terraform", "dockerfile", "cloud run"),
                "deployment rollback review",
            ),
            (
                3,
                "database or migration change",
                ("migration", "schema", "database", "sql"),
                "migration and rollback tests",
            ),
            (
                3,
                "secret or credential boundary",
                ("secret", "credential", ".env", ".pem", ".key"),
                "credential leakage review",
            ),
            (
                2,
                "dependency or lockfile change",
                ("lock", "dependency", "package.json", "pyproject.toml"),
                "dependency audit",
            ),
        ]
        for points, reason, keywords, control in signals:
            if any(keyword in signal_text for keyword in keywords):
                score += points
                reasons.append(reason)
                controls.append(control)

        score = min(score, 10)
        level = "low" if score <= 3 else "medium" if score <= 6 else "high"
        if not reasons:
            reasons.append(
                "bounded documentation change" if docs_only else "general code change"
            )
        return {
            "ok": True,
            "risk": {
                "level": level,
                "score": score,
                "reasons": list(dict.fromkeys(reasons)),
                "required_controls": list(dict.fromkeys(controls)),
                "human_approval_required": True,
                "auto_merge_allowed": False,
            },
            "files": files,
        }
    except ContractValidationError as exc:
        return _failure(exc.code, str(exc))


def _validate_contract_data(payload: Any) -> dict[str, Any]:
    contract = _exact_fields(
        payload,
        "contract",
        allowed={
            "id",
            "title",
            "goal",
            "scope",
            "acceptanceCriteria",
            "testRequirements",
            "constraints",
            "git",
            "metadata",
        },
        required={
            "id",
            "title",
            "goal",
            "scope",
            "acceptanceCriteria",
            "testRequirements",
            "constraints",
            "git",
            "metadata",
        },
    )
    scope = _exact_fields(
        contract["scope"],
        "scope",
        allowed={"description", "allowedPaths", "disallowedPaths"},
        required={"description", "allowedPaths", "disallowedPaths"},
    )
    tests = _exact_fields(
        contract["testRequirements"],
        "testRequirements",
        allowed={"commands", "mustPass"},
        required={"commands", "mustPass"},
    )
    constraints = _exact_fields(
        contract["constraints"],
        "constraints",
        allowed={"maxRetries", "timeoutMinutes", "maxCostUsd", "allowedImplementers"},
        required={"maxRetries", "timeoutMinutes", "allowedImplementers"},
    )
    git = _exact_fields(
        contract["git"],
        "git",
        allowed={"baseBranch", "branchPrefix"},
        required={"baseBranch", "branchPrefix"},
    )
    metadata = _exact_fields(
        contract["metadata"],
        "metadata",
        allowed={"createdBy", "labels"},
        required={"labels"},
    )

    allowed_paths = [
        _validate_path(path, allowed=True)
        for path in _string_list(scope["allowedPaths"], "scope.allowedPaths")
    ]
    disallowed_paths = [
        _validate_path(path, allowed=False)
        for path in _string_list(
            scope["disallowedPaths"], "scope.disallowedPaths", minimum=0
        )
    ]
    commands = [
        _validate_test_command(command)
        for command in _string_list(tests["commands"], "testRequirements.commands")
    ]
    if tests["mustPass"] is not True:
        raise ContractValidationError(
            "invalid_field", "testRequirements.mustPass must be true."
        )
    implementers = _string_list(
        constraints["allowedImplementers"], "constraints.allowedImplementers"
    )
    if any(implementer not in _ALLOWED_IMPLEMENTERS for implementer in implementers):
        raise ContractValidationError(
            "invalid_implementer", "allowedImplementers supports only codex and claude."
        )
    labels = _string_list(metadata["labels"], "metadata.labels", minimum=0)
    normalized_metadata: dict[str, Any] = {"labels": labels}
    if "createdBy" in metadata:
        normalized_metadata["createdBy"] = _required_text(
            metadata["createdBy"], "metadata.createdBy", maximum=200
        )

    normalized_constraints: dict[str, Any] = {
        "maxRetries": _integer(constraints["maxRetries"], "maxRetries", 0, 10),
        "timeoutMinutes": _integer(
            constraints["timeoutMinutes"], "timeoutMinutes", 1, 180
        ),
        "allowedImplementers": implementers,
    }
    if "maxCostUsd" in constraints:
        cost = constraints["maxCostUsd"]
        if isinstance(cost, bool) or not isinstance(cost, (int, float)) or cost <= 0:
            raise ContractValidationError(
                "invalid_field", "maxCostUsd must be a positive number."
            )
        normalized_constraints["maxCostUsd"] = cost

    return {
        "id": _validate_task_id(contract["id"]),
        "title": _required_text(contract["title"], "title", maximum=300),
        "goal": _required_text(contract["goal"], "goal"),
        "scope": {
            "description": _required_text(scope["description"], "scope.description"),
            "allowedPaths": allowed_paths,
            "disallowedPaths": disallowed_paths,
        },
        "acceptanceCriteria": _string_list(
            contract["acceptanceCriteria"], "acceptanceCriteria"
        ),
        "testRequirements": {"commands": commands, "mustPass": True},
        "constraints": normalized_constraints,
        "git": {
            "baseBranch": _validate_branch(git["baseBranch"], "git.baseBranch"),
            "branchPrefix": _validate_branch(
                git["branchPrefix"],
                "git.branchPrefix",
                allow_trailing_slash=True,
            ),
        },
        "metadata": normalized_metadata,
    }


def validate_task_contract(contract_json: str) -> dict[str, Any]:
    """Validates a JSON task contract against the orchestrator contract.

    Args:
        contract_json: Complete JSON object for one orchestrator task contract.

    Returns:
        The normalized contract or a stable validation error.
    """
    if not isinstance(contract_json, str) or not contract_json.strip():
        return _failure("missing_contract", "contract_json is required.")
    if len(contract_json.encode("utf-8")) > _MAX_CONTRACT_BYTES:
        return _failure("contract_too_large", "Contract exceeds 65536 bytes.")
    try:
        payload = json.loads(contract_json)
    except json.JSONDecodeError:
        return _failure("invalid_json", "Contract is not valid JSON.")
    try:
        return {"ok": True, "contract": _validate_contract_data(payload)}
    except ContractValidationError as exc:
        return _failure(exc.code, str(exc))


def build_task_contract(
    repository: str,
    task_id: str,
    title: str,
    goal: str,
    allowed_paths: str,
    disallowed_paths: str,
    acceptance_criteria: str,
    test_commands: str,
    base_branch: str,
    allowed_implementers: str,
    max_retries: int,
    timeout_minutes: int,
) -> dict[str, Any]:
    """Builds a strict JSON contract for nokto-agent-orchestrator.

    Args:
        repository: A github.com URL or owner/name repository identifier.
        task_id: Lowercase kebab-case id, or an empty string for a stable generated id.
        title: Short task title.
        goal: Concrete implementation goal.
        allowed_paths: Newline-separated file paths or glob patterns.
        disallowed_paths: Newline-separated blocked paths, or an empty string.
        acceptance_criteria: Newline-separated measurable requirements.
        test_commands: Newline-separated allowlisted verification commands.
        base_branch: Git base branch.
        allowed_implementers: Comma-separated list containing codex and/or claude.
        max_retries: Maximum correction retries from 0 through 10.
        timeout_minutes: Maximum task runtime from 1 through 180 minutes.

    Returns:
        A validated contract and a truthful manual handoff description.
    """
    try:
        reference = normalize_repository(repository)
        normalized_title = _required_text(title, "title", maximum=300)
        normalized_goal = _required_text(goal, "goal")
        if not isinstance(task_id, str):
            raise ContractValidationError("invalid_field", "task_id must be text.")
        if not isinstance(disallowed_paths, str):
            raise ContractValidationError(
                "invalid_field", "disallowed_paths must be text."
            )
        if not isinstance(allowed_implementers, str):
            raise ContractValidationError(
                "invalid_field", "allowed_implementers must be text."
            )
        normalized_id = (
            _validate_task_id(task_id)
            if task_id.strip()
            else _generated_task_id(
                reference.full_name, normalized_title, normalized_goal
            )
        )
        allowed = [
            _validate_path(path, allowed=True)
            for path in _parse_lines(allowed_paths, "allowed_paths")
        ]
        supplied_disallowed = (
            [
                _validate_path(path, allowed=False)
                for path in _parse_lines(disallowed_paths, "disallowed_paths")
            ]
            if disallowed_paths.strip()
            else []
        )
        criteria = _parse_lines(acceptance_criteria, "acceptance_criteria")
        commands = [
            _validate_test_command(command)
            for command in _parse_lines(test_commands, "test_commands")
        ]
        implementers = [
            item.strip().lower()
            for item in re.split(r"[,\n]", allowed_implementers)
            if item.strip()
        ]
        if not implementers or any(
            implementer not in _ALLOWED_IMPLEMENTERS for implementer in implementers
        ):
            raise ContractValidationError(
                "invalid_implementer",
                "allowed_implementers supports only codex and claude.",
            )

        risk_result = analyze_change_risk(normalized_goal, "\n".join(allowed))
        if not risk_result["ok"]:
            return risk_result
        risk = risk_result["risk"]
        contract = {
            "id": normalized_id,
            "title": normalized_title,
            "goal": normalized_goal,
            "scope": {
                "description": f"Change is limited to: {', '.join(allowed)}.",
                "allowedPaths": allowed,
                "disallowedPaths": list(
                    dict.fromkeys([*_DEFAULT_DISALLOWED_PATHS, *supplied_disallowed])
                ),
            },
            "acceptanceCriteria": criteria,
            "testRequirements": {"commands": commands, "mustPass": True},
            "constraints": {
                "maxRetries": max_retries,
                "timeoutMinutes": timeout_minutes,
                "allowedImplementers": list(dict.fromkeys(implementers)),
            },
            "git": {
                "baseBranch": base_branch,
                "branchPrefix": "agent/",
            },
            "metadata": {
                "createdBy": "nokto-adk-pr-agent",
                "labels": [
                    "google-adk",
                    "gemini",
                    "human-review-required",
                    f"risk:{risk['level']}",
                ],
            },
        }
        normalized_contract = _validate_contract_data(contract)
        return {
            "ok": True,
            "repository": reference.full_name,
            "risk": risk,
            "contract": normalized_contract,
            "contract_json": json.dumps(
                normalized_contract, ensure_ascii=False, indent=2
            ),
            "handoff": {
                "mode": "manual",
                "target": "nokto-agent-orchestrator",
                "command": "nokto-agent plan --task <task-file.json>",
                "changes_repository": False,
                "creates_pull_request": False,
                "auto_merges": False,
            },
        }
    except ContractValidationError as exc:
        return _failure(exc.code, str(exc))
    except ValueError as exc:
        return _failure("invalid_repository", str(exc))
