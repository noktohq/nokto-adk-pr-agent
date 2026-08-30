# Reproducible testing

## Test layers

| Layer | Command | External calls | Pass condition |
| --- | --- | --- | --- |
| Unit | `uv run pytest tests/unit -q` | None | All deterministic tests pass |
| Static | `agents-cli lint` | Package resolution only | Ruff, format, Codespell, and `ty` pass |
| Live agent | `agents-cli run "<prompt>"` | Vertex AI and optional GitHub reads | Agent returns truthful scoped output |
| Integration | `uv run pytest tests/integration -q` | Vertex AI and local HTTP | ADK, A2A, health, and model tests pass |
| Evaluation | `agents-cli eval run` | Vertex AI | Evaluation result is generated without runtime failure |

## Clean-machine procedure

```bash
python3 --version
uv --version
agents-cli --version
gcloud auth application-default login
gcloud auth application-default set-quota-project YOUR_PROJECT_ID
gcloud config set project YOUR_PROJECT_ID
gcloud services enable aiplatform.googleapis.com
cp .env.example .env
agents-cli install
```

Set `GOOGLE_CLOUD_PROJECT` in `.env`, then run:

```bash
uv run pytest tests/unit -q
agents-cli lint
agents-cli run "Explain your role and whether you can directly change a repository."
```

## Deterministic contract probe

```bash
uv run python - <<'PY'
from app.task_contract import build_task_contract

result = build_task_contract(
    repository="noktohq/nokto-agent-orchestrator",
    task_id="",
    title="Add reproducible test instructions",
    goal="Document the exact local validation command.",
    allowed_paths="README.md\ndocs/**",
    disallowed_paths="src/**",
    acceptance_criteria=(
        "README contains a reproducible testing section\n"
        "No source files are changed"
    ),
    test_commands="pnpm run lint\npnpm run typecheck",
    base_branch="main",
    allowed_implementers="codex,claude",
    max_retries=1,
    timeout_minutes=15,
)
assert result["ok"], result
assert result["contract"]["testRequirements"]["mustPass"] is True
assert result["contract"]["git"]["branchPrefix"] == "agent/"
assert result["handoff"]["changes_repository"] is False
print(result["contract_json"])
PY
```

This probe makes no network or model call.

## Observed baseline before custom implementation

The scaffold was installed with Python 3.13.15. `agents-cli lint` passed Ruff, formatting, Codespell, and `ty`. A live Vertex AI smoke prompt returned `NOKTO ADK READY`. These observations establish the generated baseline; rerun all checks after applying this package because the custom implementation is materially different.

## Costs

Unit and static tests do not call Gemini. Live agent, integration, and evaluation commands use Vertex AI and may incur project charges.
