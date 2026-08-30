# Hackathon submission facts

Use only after the hosted deployment and public repository are verified.

| Devpost field | Value |
| --- | --- |
| Category | Taskmaster |
| Google SDK | Agent Development Kit (ADK) |
| Google Cloud service | Cloud Run |
| Google AI model | Gemini 3.7 Flash |
| Project start date | 08-30-26 |
| Reproducible testing in README | Yes |
| Architecture diagram | `docs/architecture.png` |
| Cloud project | `nokto-agentic-2026` |
| Hosted URL | Add after deployment verification |
| Code repository | Add after public repository creation |

## New submission work

- Google ADK/Gemini agent instructions and tool orchestration
- Bounded GitHub REST inspection tools
- Deterministic change-risk engine
- Strict NOKTO orchestrator task-contract builder and validator
- ADK read-only tool guard
- Unit, integration, and behavior evaluation cases
- Cloud Run health/capability endpoints
- Architecture, security, operations, and reproducible testing documentation

## Pre-existing component disclosure

[`nokto-agent-orchestrator`](https://github.com/noktohq/nokto-agent-orchestrator) was created on 2026-07-29, before this submission project. It remains a separate downstream execution component. The new hackathon project does not claim its Claude Code/Codex worktree and PR pipeline as newly created work.

## Verification gate

Do not mark the hosted URL or public repository as complete until:

1. The repository exists and contains this implementation.
2. CI passes.
3. Cloud Run `/healthz` returns HTTP 200.
4. The hosted agent completes the README end-to-end contract prompt.
5. The architecture PNG opens correctly and is below Devpost's 35 MB limit.
