import base64
import json
import os
import unittest
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError

from app.github_tools import (
    _decode_file_content,
    inspect_github_repository,
    normalize_repository,
    validate_repository_file_path,
)


class RepositoryValidationTests(unittest.TestCase):
    def test_normalizes_https_repository(self) -> None:
        result = normalize_repository(
            "https://github.com/noktohq/nokto-agent-orchestrator.git"
        )

        self.assertEqual(result.full_name, "noktohq/nokto-agent-orchestrator")

    def test_normalizes_owner_and_repository(self) -> None:
        result = normalize_repository("noktohq/nokto-agent-orchestrator")

        self.assertEqual(result.owner, "noktohq")
        self.assertEqual(result.name, "nokto-agent-orchestrator")

    def test_rejects_non_github_host(self) -> None:
        with self.assertRaisesRegex(ValueError, "github.com"):
            normalize_repository("https://example.com/noktohq/repo")

    def test_rejects_repository_url_with_credentials(self) -> None:
        with self.assertRaisesRegex(ValueError, "credentials"):
            normalize_repository("https://token@github.com/noktohq/repo")

    def test_rejects_path_traversal(self) -> None:
        with self.assertRaisesRegex(ValueError, "relative"):
            validate_repository_file_path("../.env")

    def test_rejects_sensitive_file(self) -> None:
        with self.assertRaisesRegex(ValueError, "sensitive"):
            validate_repository_file_path("config/.env.production")

    def test_decodes_bounded_utf8_content(self) -> None:
        encoded = base64.b64encode(b"hello\n").decode("ascii")

        self.assertEqual(_decode_file_content(encoded, 32), "hello\n")

    def test_rejects_oversized_decoded_content(self) -> None:
        encoded = base64.b64encode(b"x" * 33).decode("ascii")

        with self.assertRaisesRegex(ValueError, "maximum"):
            _decode_file_content(encoded, 32)


class RepositoryPolicyTests(unittest.TestCase):
    @patch.dict(os.environ, {"GITHUB_TOKEN": "secret"}, clear=True)
    def test_token_requires_explicit_allowlist(self) -> None:
        result = inspect_github_repository("noktohq/nokto-agent-orchestrator")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "missing_repository_allowlist")

    @patch.dict(
        os.environ,
        {
            "GITHUB_TOKEN": "secret",
            "GITHUB_ALLOWED_REPOSITORIES": "noktohq/another-repo",
        },
        clear=True,
    )
    def test_token_blocks_repository_outside_allowlist(self) -> None:
        result = inspect_github_repository("noktohq/nokto-agent-orchestrator")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "repository_not_allowed")


class _FakeResponse:
    def __init__(self, payload: object, content_length: str | None = None) -> None:
        self._body = BytesIO(json.dumps(payload).encode("utf-8"))
        self.headers = {
            "Content-Length": content_length or str(len(self._body.getvalue()))
        }

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        return self._body.read(size)


class GitHubRequestTests(unittest.TestCase):
    @patch.dict(os.environ, {}, clear=True)
    @patch("app.github_tools.urlopen")
    def test_ignores_malformed_content_length_and_bounds_body(
        self, mock_urlopen
    ) -> None:
        mock_urlopen.return_value = _FakeResponse(
            {"default_branch": "main", "full_name": "noktohq/repo"},
            content_length="invalid",
        )

        result = inspect_github_repository("noktohq/repo")

        self.assertTrue(result["ok"])

    @patch.dict(os.environ, {}, clear=True)
    @patch("app.github_tools.urlopen")
    def test_repository_metadata_is_bounded_and_read_only(self, mock_urlopen) -> None:
        mock_urlopen.return_value = _FakeResponse(
            {
                "archived": False,
                "default_branch": "main",
                "description": "Example",
                "fork": False,
                "full_name": "noktohq/repo",
                "html_url": "https://github.com/noktohq/repo",
                "language": "Python",
                "license": {"spdx_id": "MIT"},
                "open_issues_count": 0,
                "topics": ["adk"],
                "updated_at": "2026-08-30T00:00:00Z",
                "visibility": "public",
                "private_field": "must not be returned",
            }
        )

        result = inspect_github_repository("noktohq/repo")

        self.assertTrue(result["ok"])
        self.assertTrue(result["read_only"])
        self.assertNotIn("private_field", result["repository"])
        request = mock_urlopen.call_args.args[0]
        self.assertIsNone(request.get_header("Authorization"))

    @patch.dict(os.environ, {}, clear=True)
    @patch("app.github_tools.urlopen")
    def test_404_maps_to_stable_error(self, mock_urlopen) -> None:
        mock_urlopen.side_effect = HTTPError(
            "https://api.github.com/repos/noktohq/missing",
            404,
            "Not Found",
            hdrs=None,
            fp=None,
        )

        result = inspect_github_repository("noktohq/missing")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "github_not_found")


if __name__ == "__main__":
    unittest.main()
