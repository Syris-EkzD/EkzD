# EkzD

EkzD is a thin deterministic trust layer between task planning and code review.

It freezes implementation authority, gives a portable worker a deterministic structural checker,
and lets the maintainer independently verify the exact returned candidate. It does not run project
toolchains or replace CI.

## Responsibility split

EkzD verifies structural evidence:

- exact frozen baseline and implementation branch;
- history ancestry and the fixed 50-commit safety ceiling;
- allowed, excluded, and protected paths across current state and intermediate history;
- clean final candidate state and exact candidate binding;
- frozen contract, handoff, and runtime identity.

CI or another external verifier owns dependency-bound correctness:

- formatting;
- lint/static analysis;
- type checking;
- unit/integration tests;
- builds;
- framework/package/deployment checks.

Human review owns semantic correctness, architecture, maintainability, and merge approval.

A completed implementation should therefore satisfy all three layers for the same exact published
candidate: EkzD structural PASS, required CI PASS, and maintainer/human approval.

## Project setup

Run once inside a conventional Git checkout:

```sh
ekzd init
```

The generated `.ekzd/project.toml` is durable policy:

```toml
schema_version = 4
name = "Example"
protected = []
exclude = []
guidance = []
```

Project fields are `schema_version = 4`, a non-empty `name`, and optional `protected`,
`exclude`, and `guidance` arrays. Project-specific readiness/verification commands are
intentionally not part of EkzD.

Commit the policy and generated ignore rule:

```sh
git add .ekzd/project.toml .gitignore
git commit -m "chore: configure EkzD policy"
```

`.ekzd/local/` remains ignored and untracked.

## Write a disposable task

Keep task drafts under `.ekzd/local/`:

```toml
schema_version = 3
objective = "Add registration validation"
branch = "feat/registration-validation"
include = ["src/registration/", "tests/registration/"]
exclude = []
sources = ["src/registration/model.py"]
acceptance = [
  "Invalid usernames are rejected.",
  "Existing valid usernames still pass.",
]
guidance = ["Keep this implementation dependency-free."]
```

Required task fields are `schema_version`, `objective`, `branch`, non-empty `include`, and
non-empty `acceptance`. Task exclusions extend project exclusions. Project protections cannot be
removed. Project and task guidance are additive. Declared source files must exist as regular files
at the frozen baseline.

The frozen contract always uses EkzD's fixed 50-commit safety ceiling. It is not configurable.

## Start and transfer the handoff

From a clean baseline:

```sh
ekzd start .ekzd/local/task.toml
```

EkzD freezes the exact baseline, implementation branch, scope, acceptance/guidance, runtime
identity, and commit ceiling, then creates:

```text
.ekzd/local/handoffs/<contract-id>/handoff.zip
```

The archive contains `instructions.md`, `contract.json`, `run.py`, `handoff.json`, and the
pinned `runtime/ekzd/*.py` source. The worker needs only a target repository checkout, Linux,
Python 3.11+, and Git. Project SDKs/dependencies are not required for EkzD itself.

## Worker flow

Create the declared branch from the exact frozen baseline:

```sh
git switch -c feat/registration-validation <baseline-sha>
python3 /path/to/handoff/run.py prepare --json
python3 /path/to/handoff/run.py check --json
```

Implement and self-review. Use iterative EkzD checks to catch structural violations. Make small,
meaningful, logically focused commits. Prefer Conventional Commits such as
`feat(scope): description` where a useful scope exists; omit the scope when it adds no useful
information. Avoid giant unrelated commits and noisy WIP/checkpoint/fixup commits.

When complete:

```sh
python3 /path/to/handoff/run.py check --final --json
```

Then publish/update the implementation branch and PR. The implementation session is not complete
merely because EkzD passes. Wait for all required CI checks on the exact published candidate. If CI
fails, inspect the CI evidence, fix within frozen authority, rerun EkzD, publish the new candidate,
and wait again. If CI cannot be observed or required checks cannot run, report that blocker instead
of claiming completion.

EkzD itself does not push, create PRs, query CI, or merge. Those belong to the surrounding workflow
or future orchestrator.

## Maintainer verification and acceptance

After obtaining the exact worker candidate:

```sh
git switch feat/registration-validation
ekzd verify
# Review the exact verified candidate and independently confirm required CI for that commit.
ekzd finish --accept
```

Maintainer verification independently rechecks structural authority and binds evidence to the exact
candidate. If the candidate changes, the evidence becomes stale.

`finish --accept` records explicit acceptance only; it does not query CI. The surrounding
maintainer/orchestrator must ensure required CI has passed for the same exact commit before merge.

To abandon an active task:

```sh
ekzd abort
```

## Commands

```sh
ekzd --help
ekzd --version
ekzd init
ekzd start path/to/task.toml
ekzd status
ekzd handoff [--output /outside/repository/handoff.zip]
ekzd verify
ekzd finish --accept
ekzd abort
```

## Compatibility

Project schema 4, task schema 3, and contract schema 3 intentionally remove project/task
readiness/verification command execution. Pre-1.0 older authoring/session/handoff authority must be
refrozen with the current schemas; there is no migration layer.

## Boundaries

EkzD deliberately does not provide:

- model/API or worker orchestration;
- dependency/package/toolchain installation;
- project formatting/lint/test/build execution;
- repository cloning;
- automatic commits, pushes, pull requests, CI polling, merges, or deployments;
- semantic code-quality judgment;
- operating-system sandboxing.

Linux, Python 3.11+, Git, repository transport, project CI, and other project capabilities are
provisioned externally.

See [VERIFICATION.md](VERIFICATION.md) for the exact trust and evidence model.

## Development

EkzD's own repository correctness is checked by GitHub Actions:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m compileall -q src tests
```
