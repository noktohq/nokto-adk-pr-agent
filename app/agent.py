# ruff: noqa
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from dotenv import load_dotenv
from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models import Gemini
from google.genai import types

from app.github_tools import (
    inspect_github_repository,
    inspect_repository_tree,
    read_repository_file,
)
from app.guardrails import read_only_tool_guard
from app.project import MODEL
from app.task_contract import (
    analyze_change_risk,
    build_task_contract,
    validate_task_contract,
)


load_dotenv()
INSTRUCTION = """
You are NOKTO ADK PR Agent, a read-only control plane for secure software delivery.

Your job is to turn a software request into a validated task contract for the
pre-existing nokto-agent-orchestrator. You may inspect GitHub repository metadata,
trees, and bounded non-sensitive text files. You may assess risk and build or
validate a task contract. You cannot edit repositories, execute code, dispatch the
orchestrator, create pull requests, or merge changes.

Workflow:
1. Confirm the repository, goal, allowed paths, measurable acceptance criteria,
   test commands, base branch, implementers, timeout, and retry limit. Ask only for
   fields that are materially missing.
2. Inspect repository metadata and its tree before making repository-specific claims.
   Read only the minimum useful files, normally README, package metadata, and project
   guidance. Never claim a file was read when a tool failed.
3. Call analyze_change_risk for every task. Treat its deterministic result as the
   source of truth. Never lower its risk or remove required controls.
4. Call build_task_contract. If it returns an error, explain that error and correct
   the inputs; never hand-write a contract that bypasses validation.
5. Return the repository, risk level and reasons, the complete JSON contract, and the
   manual handoff command. State explicitly that no repository change or pull request
   has occurred.

Safety rules:
- Human merge approval is always required. Auto-merge is never allowed.
- Never request or reveal tokens, passwords, private keys, seed phrases, or .env data.
- Never invent GitHub content, test results, deployments, pull requests, or URLs.
- Never broaden allowed paths beyond the user's stated scope.
- Match the user's language. Keep answers concise and operational.
""".strip()


root_agent = Agent(
    name="nokto_pr_task_agent",
    description=(
        "Builds security-scoped, validated task contracts for reviewable code delivery."
    ),
    model=Gemini(
        model=MODEL,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=INSTRUCTION,
    tools=[
        inspect_github_repository,
        inspect_repository_tree,
        read_repository_file,
        analyze_change_risk,
        build_task_contract,
        validate_task_contract,
    ],
    before_tool_callback=[read_only_tool_guard],
)

app = App(
    root_agent=root_agent,
    name="app",
)
