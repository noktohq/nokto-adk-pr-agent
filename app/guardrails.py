import json
from typing import Any

READ_ONLY_TOOLS = frozenset(
    {
        "analyze_change_risk",
        "build_task_contract",
        "inspect_github_repository",
        "inspect_repository_tree",
        "read_repository_file",
        "validate_task_contract",
    }
)

_MAX_TOOL_ARGUMENT_BYTES = 32_768


async def read_only_tool_guard(
    *, tool: Any, args: Any, tool_context: Any
) -> dict[str, Any] | None:
    """Blocks every tool outside the agent's explicit read-only allowlist."""
    del tool_context
    tool_name = getattr(tool, "name", "") or ""
    if tool_name not in READ_ONLY_TOOLS:
        return {
            "error": f"Tool '{tool_name or 'unknown'}' is not permitted.",
            "error_code": "tool_not_allowed",
            "permission_denied": True,
        }

    try:
        encoded_args = json.dumps(
            args, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
    except (TypeError, ValueError):
        return {
            "error": "Tool arguments must be JSON serializable.",
            "error_code": "invalid_tool_arguments",
            "permission_denied": True,
        }

    if len(encoded_args) > _MAX_TOOL_ARGUMENT_BYTES:
        return {
            "error": "Tool arguments exceed the 32768-byte limit.",
            "error_code": "tool_arguments_too_large",
            "permission_denied": True,
        }
    return None
