# EkzD

EkzD is a small, project-agnostic development harness for scoped and verifiable AI-assisted work.

It does not run an AI model, orchestrate agents, replace Git, or replace CI. Its job is narrower: define the working contract for a task, expose that contract to the tools or people doing the work, keep each session bounded, run deterministic local verification, and refuse acceptance unless the verified state still matches the state being accepted.

EkzD is deliberately not the code generator. In the intended V1 workflow, an AI coding session, Codex, or a human produces the changes; EkzD provides the contract and deterministic acceptance gate around that work.

## Recommended V1 workflow

EkzD V1 is designed to work well with separate planning/prompt-generation and code-generation sessions without requiring a daemon, agent router, or model integration.

```text
You define the objective
        ↓
ekzd start "..."
        ↓
ekzd context --json
        ↓
Prompt-generator session
        ↓
implementation prompt
        ↓
Code-generator session
        ↓
GitHub branch / commits
        ↓
GitHub CI
        ↓
you pull the latest branch locally
        ↓
ekzd verify
        ↓
human review
        ↓
ekzd finish --accept
        ↓
merge
```

Responsibilities are intentionally separated:

- **You** choose the objective, approve scope changes, review the result, and retain final merge authority.
- **EkzD** defines the local session contract and deterministically checks the Git-visible result against that contract.
- **Prompt-generator session** converts the EkzD context and objective into a focused implementation prompt without expanding scope.
- **Code-generator session** implements the prompt and writes the actual code, usually on a dedicated GitHub branch.
- **GitHub** carries branches, commits, pull requests, and collaboration history.
- **CI** independently executes remote checks where appropriate.

The active session state lives in `.ekzd/session.json`, which is intentionally local and gitignored. A coding session operating only through GitHub cannot automatically see that local state. V1 bridges that gap explicitly through `ekzd context --json`, which can be handed to the prompt-generator session as the authoritative task context.

This is a manual bridge by design. V1 does not claim to control an AI's GitHub actions in real time. Instead, it validates the resulting Git state after that work is pulled into the environment where EkzD is running.

## V1 commands

```sh
ekzd init
ekzd start "implement registration validation"
ekzd context
ekzd context --json
ekzd verify
ekzd handoff --done "implemented validation" --next "review edge cases"
ekzd finish --accept
```

If an active session cannot or should not be accepted, close it without acceptance:

```sh
ekzd abort
```

`abort` changes only local EkzD session state. It does not revert project files, commits, or Git history, and it never records acceptance.

`ekzd init` creates `.ekzd/project.toml` and ignores `.ekzd/session.json`. The project configuration is committed; session state is local and disposable.

## Terminal interface

EkzD uses a compact terminal interface inspired by modern coding CLIs: semantic status colors, concise section headings, and readable success/failure indicators rather than raw JSON for normal human-facing output.

- green: successful or clean state;
- red: failure or blocked operation;
- yellow: warning or aborted/non-accepted state;
- cyan: headings, accents, and informational markers;
- dim text: secondary details.

Colors are enabled automatically only for interactive terminals. They are disabled for redirected/piped output, when `TERM=dumb`, or when the standard `NO_COLOR` environment variable is present. `ekzd context --json` always remains machine-readable JSON without ANSI styling.

EkzD borrows the visual hierarchy of tools such as Codex, but V1 remains a command-oriented CLI rather than a full-screen interactive TUI.

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

Configuration is intentionally explicit. Empty `scope.include`, empty acceptance criteria, or an empty verification plan prevent a session from starting. A deliberately repo-wide scope must still be explicit, for example `include = ["*"]`.

The complete `.ekzd/project.toml` digest is captured when a session starts. Changing scope, authority, session policy, acceptance criteria, verification commands, or any other project harness configuration during an active session invalidates that session. Abort it and start a fresh session after making configuration changes.

`session.max_commits` is a hard session-size budget. `ekzd init` writes `3`, and V1 also treats a missing value as `3` for backward compatibility. Projects may choose another positive integer when a genuinely larger bounded task needs it. The configured value is captured when the session starts; it cannot be raised mid-session to excuse work that has already exceeded its original boundary.

The intent is not to claim that a fourth commit automatically lowers code quality. The budget creates a practical stopping point before one objective grows into several loosely related tasks. Configure the budget to fit the project, but keep one EkzD session focused on one objective.

## Acceptance model

EkzD treats verification and acceptance as different stages.

`ekzd verify`:

1. validates the project configuration;
2. requires it to match the configuration captured when the session started;
3. enforces the active session's commit budget;
4. checks changed paths against `scope.include` and `scope.exclude` before verification;
5. runs verification commands without a shell;
6. reloads the ready project configuration and active session after the commands finish, then rechecks the frozen configuration, commit budget, and path scope;
7. records the exact Git-visible state, final session budget, and configuration digest that passed.

`ekzd finish --accept` succeeds only when:

- verification passed;
- the project configuration still matches the active session and the verified configuration;
- the session remains within its original commit budget;
- the Git HEAD, branch, worktree state, and Git-visible file contents still match the verified state;
- scope rules still pass; and
- the operator explicitly supplies `--accept` after the required review.

`--accept` is an operator attestation. V1 does not authenticate who typed the command or cryptographically prove that a particular human performed the review.

Acceptance requires the current Git-visible state to exactly match the verified state. If the current state differs, verification must be rerun before acceptance can succeed.

The exact-state fingerprint covers Git-visible tracked, staged, unstaged, and untracked files. Git-ignored files are outside V1's fingerprint unless a configured verification command explicitly checks them.

## Context and handoff

`ekzd context` renders a human-friendly view of the current objective, session policy, Git state, scope, authority, acceptance criteria, and verification plan. `--json` provides the context as structured data for prompt generators and other tools.

The `sources.paths` entries define the project material an AI or developer is expected to consult. EkzD exposes those paths as part of the contract; V1 does not semantically read or enforce their prose by itself.

Likewise, free-text authority and constraint entries are contextual instructions for the AI or human operator. EkzD mechanically enforces what it can measure: configuration integrity, Git-visible scope, commit budget, verification results, exact-state binding, and explicit acceptance.

`ekzd handoff` records semantic progress (`--done`) and next actions (`--next`) in the local session state. It does not modify project documentation automatically.

## Boundaries

V1 deliberately stays small:

- no daemon;
- no database;
- no model/API integration;
- no network operations;
- no autonomous commits, pushes, merges, or deployments;
- no real-time interception of an AI's GitHub writes;
- no claim that free-text authority rules are mechanically understood;
- no claim that deterministic checks can prove semantic correctness.

GitHub Actions can provide remote verification for repositories that need dependencies or services unavailable in a local/session environment. EkzD remains the harness; CI remains an independent execution/verification service.

## Development

EkzD requires Python 3.11+ and Git.

Install it from the repository with:

```sh
python3 -m pip install .
ekzd --help
```

Run its tests with:

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q src tests
```

The repository history before EkzD is retained as the historical WhiteTree Workflow Manager experiment. The current implementation replaces that mechanism rather than extending it.
