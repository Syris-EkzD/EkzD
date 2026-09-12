# EkzD

EkzD is a small, project-agnostic development harness for scoped and verifiable AI-assisted work.

It does not run an AI model, orchestrate agents, replace Git, or replace CI. Its job is narrower: define the working frame for a task, expose the relevant project context, keep each session bounded, run deterministic local verification, and refuse acceptance unless the verified state still matches the state being accepted.

## V1 commands

```sh
ekzd init
ekzd start "implement registration validation"
ekzd context
ekzd verify
ekzd handoff --done "implemented validation" --next "review edge cases"
ekzd finish --accept
```

`ekzd init` creates `.ekzd/project.toml` and ignores `.ekzd/session.json`. The project configuration is committed; session state is local and disposable.

## Project configuration

Example:

```toml
schema_version = 1

[project]
name = "Nosas"

[sources]
paths = ["README.md", "docs/architecture.md"]

[scope]
include = ["apps/web/src/", "tests/"]
exclude = ["apps/web/src/generated/"]
constraints = ["Avoid unrelated changes."]

[authority]
may = ["Edit scoped source and tests."]
requires_approval = ["Merge to main."]
may_not = ["Change secrets or deployment credentials."]

[session]
max_commits = 3

[acceptance]
criteria = [
  "Configured verification passes.",
  "The requested behavior is reviewed before acceptance.",
]

[[verification.steps]]
name = "tests"
command = ["python3", "-m", "unittest", "discover", "-s", "tests", "-v"]
cwd = "."
timeout_seconds = 600
```

Configuration is intentionally explicit. Empty acceptance criteria or an empty verification plan prevent a session from starting.

`session.max_commits` is a hard session-size budget. `ekzd init` writes `3`, and V1 also treats a missing value as `3` for backward compatibility. Projects may choose another positive integer when a genuinely larger bounded task needs it. The configured value is captured when the session starts; it cannot be raised mid-session to excuse work that has already exceeded its original boundary.

The intent is not to claim that a fourth commit automatically lowers code quality. The budget creates a practical stopping point before one objective grows into several loosely related tasks. Configure the budget to fit the project, but keep one EkzD session focused on one objective.

## Acceptance model

EkzD treats verification and acceptance as different stages.

`ekzd verify`:

1. validates the project configuration;
2. enforces the active session's commit budget;
3. checks changed paths against `scope.include` and `scope.exclude`;
4. runs verification commands without a shell;
5. records the exact Git/worktree state, session budget, and configuration digest that passed.

`ekzd finish --accept` succeeds only when:

- verification passed;
- the session remains within its original commit budget;
- the project configuration is unchanged since verification;
- the Git HEAD, branch, worktree state, and Git-visible file contents are unchanged since verification;
- scope rules still pass; and
- a human explicitly supplies `--accept`.

Any change after verification invalidates acceptance until verification is rerun. A green command result is evidence, not automatic approval.

## Context and handoff

`ekzd context` renders the current objective, session policy, Git state, sources, scope, authority, acceptance criteria, and verification plan. `--json` provides the same information as structured data for other tools.

`ekzd handoff` records semantic progress (`--done`) and next actions (`--next`) in the local session state. It does not modify project documentation automatically.

## Boundaries

V1 deliberately stays small:

- no daemon;
- no database;
- no model/API integration;
- no network operations;
- no autonomous commits, pushes, merges, or deployments;
- no claim that deterministic checks can prove semantic correctness.

GitHub Actions can provide remote verification for repositories that need dependencies or services unavailable in a local/session environment. EkzD remains the harness; CI remains the execution/verification service.

## Development

EkzD requires Python 3.11+ and Git.

Run its tests with:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m compileall -q src tests
```

The repository history before EkzD is retained as the historical WhiteTree Workflow Manager experiment. The current implementation replaces that mechanism rather than extending it.
