# Security policy

## Supported version

Security fixes are applied to the latest `main` branch.

## Reporting

Report suspected vulnerabilities privately to `edin@nokto.no`. Do not include production tokens, credentials, or personal data in the report.

## Security model

The hosted ADK agent is read-only. It can inspect allowlisted GitHub context, assess risk, and build a task contract. It cannot modify a repository, dispatch the downstream orchestrator, create a pull request, or merge code.

Authenticated GitHub reads fail closed unless `GITHUB_ALLOWED_REPOSITORIES` explicitly permits the target. Store `GITHUB_TOKEN` in Google Secret Manager and grant access only to the Cloud Run service account.

See [docs/architecture.md](docs/architecture.md) for trust boundaries, limits, and failure behavior.

## Known limitations

- Unauthenticated GitHub inspection is subject to GitHub's public API limits.
- In-memory sessions are not durable across restarts.
- The manual handoff is intentional; this version has no remote orchestrator dispatch endpoint.
- Live Gemini output is non-deterministic and is covered by behavior evaluations rather than exact-text unit tests.
