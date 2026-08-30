from __future__ import annotations

import base64
import binascii
import json
import os
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen

_API_BASE = "https://api.github.com"
_API_VERSION = "2026-03-10"
_REQUEST_TIMEOUT_SECONDS = 10
_MAX_API_RESPONSE_BYTES = 2_000_000
_MAX_FILE_BYTES = 65_536
_MAX_TREE_ENTRIES = 2_500
_REPOSITORY_PART = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
_REF = re.compile(r"^[A-Za-z0-9._/-]{1,255}$")
_BINARY_SUFFIXES = {
    ".7z",
    ".avi",
    ".bin",
    ".bmp",
    ".class",
    ".dll",
    ".dylib",
    ".exe",
    ".gif",
    ".gz",
    ".ico",
    ".jar",
    ".jpeg",
    ".jpg",
    ".mov",
    ".mp3",
    ".mp4",
    ".o",
    ".pdf",
    ".png",
    ".pyc",
    ".so",
    ".tar",
    ".webm",
    ".webp",
    ".woff",
    ".woff2",
    ".zip",
}
_SENSITIVE_BASENAMES = {
    ".npmrc",
    ".pypirc",
    "credentials.json",
    "id_ed25519",
    "id_rsa",
    "service-account.json",
}


@dataclass(frozen=True)
class RepositoryReference:
    owner: str
    name: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"

    @property
    def url(self) -> str:
        return f"https://github.com/{self.full_name}"


class GitHubToolError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def normalize_repository(repository: str) -> RepositoryReference:
    """Normalizes a github.com repository URL or owner/name identifier."""
    value = repository.strip()
    if not value or len(value) > 300:
        raise ValueError("Repository must be between 1 and 300 characters.")

    if "://" in value:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or (parsed.hostname or "").lower() != "github.com":
            raise ValueError("Only HTTPS repositories on github.com are supported.")
        if parsed.username or parsed.password:
            raise ValueError("Repository URLs must not contain credentials.")
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("Repository URL contains an invalid port.") from exc
        if port not in (None, 443):
            raise ValueError("Repository URLs must use the standard HTTPS port.")
        if parsed.query or parsed.fragment:
            raise ValueError(
                "Repository URLs must not contain query strings or fragments."
            )
        parts = [part for part in parsed.path.split("/") if part]
    else:
        if any(character in value for character in "?#@\\"):
            raise ValueError("Repository identifier contains unsupported characters.")
        parts = value.split("/")

    if len(parts) != 2:
        raise ValueError("Repository must use the format owner/name.")
    owner, name = parts
    if name.endswith(".git"):
        name = name[:-4]
    if not _REPOSITORY_PART.fullmatch(owner) or not _REPOSITORY_PART.fullmatch(name):
        raise ValueError("Repository owner and name contain invalid characters.")
    if owner in {".", ".."} or name in {".", ".."}:
        raise ValueError("Repository owner and name must be explicit.")
    return RepositoryReference(owner=owner, name=name)


def validate_repository_file_path(path: str) -> str:
    """Validates a bounded, repository-relative text file path."""
    value = path.strip()
    if not value or len(value) > 500 or "\x00" in value or "\\" in value:
        raise ValueError("File path must be a valid repository-relative path.")
    candidate = PurePosixPath(value)
    if candidate.is_absolute() or any(
        part in {"", ".", ".."} for part in candidate.parts
    ):
        raise ValueError("File path must be repository-relative without traversal.")

    basename = candidate.name.lower()
    if (
        basename == ".env"
        or basename.startswith(".env.")
        or basename in _SENSITIVE_BASENAMES
        or candidate.suffix.lower() in {".key", ".p12", ".pem", ".pfx"}
        or "secrets" in {part.lower() for part in candidate.parts}
    ):
        raise ValueError("Reading sensitive repository files is blocked.")
    if candidate.suffix.lower() in _BINARY_SUFFIXES:
        raise ValueError("Only UTF-8 text files are supported.")
    return candidate.as_posix()


def _validate_ref(ref: str) -> str:
    value = ref.strip()
    if (
        not _REF.fullmatch(value)
        or ".." in value
        or value.startswith("/")
        or value.endswith("/")
        or "@{" in value
    ):
        raise ValueError("Git ref contains invalid characters.")
    return value


def _failure(code: str, message: str, *, retryable: bool = False) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {"code": code, "message": message, "retryable": retryable},
    }


def _allowed_patterns() -> list[str]:
    return [
        value.strip().lower()
        for value in os.getenv("GITHUB_ALLOWED_REPOSITORIES", "").split(",")
        if value.strip()
    ]


def _enforce_repository_policy(repository: RepositoryReference) -> str | None:
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if not token:
        return None

    patterns = _allowed_patterns()
    if not patterns:
        raise GitHubToolError(
            "missing_repository_allowlist",
            "GITHUB_ALLOWED_REPOSITORIES is required when GITHUB_TOKEN is set.",
        )
    full_name = repository.full_name.lower()
    permitted = full_name in patterns or any(
        pattern.endswith("/*") and full_name.startswith(pattern[:-1])
        for pattern in patterns
    )
    if not permitted:
        raise GitHubToolError(
            "repository_not_allowed",
            "Repository is outside GITHUB_ALLOWED_REPOSITORIES.",
        )
    return token


def _request_json(path: str, *, max_bytes: int = _MAX_API_RESPONSE_BYTES) -> Any:
    if not path.startswith("/repos/"):
        raise GitHubToolError("invalid_api_path", "GitHub API path is not permitted.")

    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "nokto-adk-pr-agent/0.1.0",
        "X-GitHub-Api-Version": _API_VERSION,
    }
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = Request(f"{_API_BASE}{path}", headers=headers, method="GET")
    try:
        with urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
            content_length = response.headers.get("Content-Length")
            if content_length:
                try:
                    declared_length = int(content_length)
                except (TypeError, ValueError):
                    declared_length = None
                if declared_length is not None and declared_length > max_bytes:
                    raise GitHubToolError(
                        "response_too_large",
                        "GitHub response exceeds the configured limit.",
                    )
            body = response.read(max_bytes + 1)
    except HTTPError as exc:
        if exc.code == 404:
            raise GitHubToolError(
                "github_not_found", "Repository resource was not found."
            ) from exc
        if exc.code in {401, 403}:
            raise GitHubToolError(
                "github_access_denied",
                "GitHub denied the request or the API rate limit was reached.",
                retryable=exc.code == 403,
            ) from exc
        if exc.code == 429 or exc.code >= 500:
            raise GitHubToolError(
                "github_temporarily_unavailable",
                "GitHub is temporarily unavailable.",
                retryable=True,
            ) from exc
        raise GitHubToolError("github_http_error", "GitHub request failed.") from exc
    except (TimeoutError, URLError) as exc:
        raise GitHubToolError(
            "github_network_error",
            "GitHub request timed out or failed.",
            retryable=True,
        ) from exc

    if len(body) > max_bytes:
        raise GitHubToolError(
            "response_too_large", "GitHub response exceeds the configured limit."
        )
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GitHubToolError(
            "invalid_github_response", "GitHub returned an invalid JSON response."
        ) from exc


def _decode_file_content(encoded: str, max_bytes: int = _MAX_FILE_BYTES) -> str:
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("GitHub returned invalid base64 file content.") from exc
    if len(raw) > max_bytes:
        raise ValueError(f"File exceeds the {max_bytes}-byte maximum.")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Repository file is not valid UTF-8 text.") from exc


def inspect_github_repository(repository: str) -> dict[str, Any]:
    """Reads safe metadata for one GitHub repository without modifying it.

    Args:
        repository: A github.com URL or owner/name repository identifier.

    Returns:
        A JSON-compatible result containing selected repository metadata.
    """
    try:
        reference = normalize_repository(repository)
        _enforce_repository_policy(reference)
        payload = _request_json(f"/repos/{reference.owner}/{reference.name}")
        if not isinstance(payload, dict):
            raise GitHubToolError(
                "invalid_github_response", "GitHub returned an unexpected response."
            )
        selected = {
            "archived": bool(payload.get("archived")),
            "default_branch": payload.get("default_branch"),
            "description": payload.get("description"),
            "fork": bool(payload.get("fork")),
            "full_name": payload.get("full_name"),
            "html_url": payload.get("html_url"),
            "language": payload.get("language"),
            "license": (payload.get("license") or {}).get("spdx_id"),
            "open_issues_count": payload.get("open_issues_count"),
            "topics": payload.get("topics") or [],
            "updated_at": payload.get("updated_at"),
            "visibility": payload.get("visibility"),
        }
        if not isinstance(selected["default_branch"], str):
            raise GitHubToolError(
                "invalid_github_response", "GitHub response has no default branch."
            )
        return {
            "ok": True,
            "source": "github_rest_api",
            "repository": selected,
            "read_only": True,
        }
    except GitHubToolError as exc:
        return _failure(exc.code, str(exc), retryable=exc.retryable)
    except ValueError as exc:
        return _failure("invalid_repository", str(exc))


def inspect_repository_tree(repository: str, ref: str) -> dict[str, Any]:
    """Lists a bounded Git tree for a repository without reading file contents.

    Args:
        repository: A github.com URL or owner/name repository identifier.
        ref: Branch, tag, or commit. Pass an empty string for the default branch.

    Returns:
        A JSON-compatible result with at most 2500 tree entries.
    """
    metadata = inspect_github_repository(repository)
    if not metadata.get("ok"):
        return metadata
    try:
        reference = normalize_repository(repository)
        selected_ref = _validate_ref(
            ref or str(metadata["repository"]["default_branch"])
        )
        path = (
            f"/repos/{reference.owner}/{reference.name}/git/trees/"
            f"{quote(selected_ref, safe='')}?{urlencode({'recursive': '1'})}"
        )
        payload = _request_json(path)
        if not isinstance(payload, dict) or not isinstance(payload.get("tree"), list):
            raise GitHubToolError(
                "invalid_github_response",
                "GitHub returned an unexpected tree response.",
            )
        entries = []
        for entry in payload["tree"][:_MAX_TREE_ENTRIES]:
            if not isinstance(entry, dict):
                continue
            entries.append(
                {
                    "path": entry.get("path"),
                    "size": entry.get("size"),
                    "type": entry.get("type"),
                }
            )
        return {
            "ok": True,
            "repository": reference.full_name,
            "ref": selected_ref,
            "entries": entries,
            "returned_entries": len(entries),
            "truncated": bool(payload.get("truncated"))
            or len(payload["tree"]) > _MAX_TREE_ENTRIES,
            "read_only": True,
        }
    except GitHubToolError as exc:
        return _failure(exc.code, str(exc), retryable=exc.retryable)
    except ValueError as exc:
        return _failure("invalid_tree_request", str(exc))


def read_repository_file(repository: str, path: str, ref: str) -> dict[str, Any]:
    """Reads one bounded UTF-8 text file from a GitHub repository.

    Args:
        repository: A github.com URL or owner/name repository identifier.
        path: Repository-relative text file path. Sensitive files are blocked.
        ref: Branch, tag, or commit. Pass an empty string for the default branch.

    Returns:
        A JSON-compatible result with the selected file content.
    """
    metadata = inspect_github_repository(repository)
    if not metadata.get("ok"):
        return metadata
    try:
        reference = normalize_repository(repository)
        selected_path = validate_repository_file_path(path)
        selected_ref = _validate_ref(
            ref or str(metadata["repository"]["default_branch"])
        )
        query = urlencode({"ref": selected_ref})
        api_path = (
            f"/repos/{reference.owner}/{reference.name}/contents/"
            f"{quote(selected_path, safe='/')}?{query}"
        )
        payload = _request_json(api_path, max_bytes=200_000)
        if (
            not isinstance(payload, dict)
            or payload.get("type") != "file"
            or payload.get("encoding") != "base64"
            or not isinstance(payload.get("content"), str)
        ):
            raise GitHubToolError(
                "invalid_github_response", "GitHub resource is not a base64 text file."
            )
        content = _decode_file_content(payload["content"])
        return {
            "ok": True,
            "repository": reference.full_name,
            "path": selected_path,
            "ref": selected_ref,
            "sha": payload.get("sha"),
            "size": len(content.encode("utf-8")),
            "content": content,
            "read_only": True,
        }
    except GitHubToolError as exc:
        return _failure(exc.code, str(exc), retryable=exc.retryable)
    except ValueError as exc:
        return _failure("invalid_file_request", str(exc))
