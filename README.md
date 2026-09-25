# EkzD

EkzD is a small, project-agnostic development harness for scoped and verifiable AI-assisted work.

It freezes durable project policy plus disposable task intent into one contract, creates a portable pinned worker handoff, checks the returned candidate, and requires explicit maintainer acceptance. It does not run an AI model, install dependencies, clone repositories, push branches, or merge pull requests.

## Workflow

The supported workflow is:

```text
1. init project once
2. configure and commit durable project policy
3. write a disposable local task
4. start the task
5. transfer the generated handoff archive
6. worker prepare → check → check --final
7. maintainer verify → review → finish --accept
```

A new task should not require another project-policy commit unless the durable policy itself changes.

## 1. Initialize project policy

Run once inside a conventional Git checkout:

```sh
ekzd init
```

The generated `.ekzd/project.toml` is intentionally not ready to start a task until at least one required verification step is configured:

```toml
schema_version = 3
name = "Example"
protected = []
exclude = []
guidance = []
# Add at least one required verification step before starting a task.
#
# [[verification]]
# name = "tests"
# command = ["..."]
# cwd = "."
# timeout_seconds = 600
```

Uncomment or add one or more `[[verification]]` tables directly; there is no conflicting placeholder to remove.

A configured project can also declare optional additive readiness probes:

```toml
[[readiness]]
name = "python"
command = ["python3", "--version"]
timeout_seconds = 30

[[verification]]
name = "tests"
command = ["python3", "-m", "unittest", "discover", "-s", "tests", "-v"]
cwd = "."
timeout_seconds = 600
```

Commit the durable policy and generated ignore rule:

```sh
git add .ekzd/project.toml .gitignore
git commit -m "chore: configure EkzD policy"
```

`.ekzd/local/` remains ignored and untracked.

Project fields are:

- `schema_version = 3`
- non-empty `name`
- optional `protected`, `exclude`, and `guidance` arrays
- optional `[[readiness]]` probes
- at least one required `[[verification]]` step before a task can start

Verification/readiness steps use direct argv arrays without a shell. `cwd` defaults to `.`; `timeout_seconds` defaults to 600 and may not exceed 3600.

## 2. Write a disposable task

Keep task drafts under the ignored local directory, for example `.ekzd/local/task.toml`:

```toml
schema_version = 2
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

# Optional additive task-specific checks.
[[verification]]
name = "focused-tests"
command = ["python3", "-m", "unittest", "tests.registration.test_validation"]
```

Required task fields are `schema_version`, `objective`, `branch`, non-empty `include`, and non-empty `acceptance`.

Task exclusions extend project exclusions. Project protections cannot be removed by a task. Guidance, readiness, and verification are additive. The frozen contract always uses EkzD's fixed 50-commit safety ceiling; project and task files cannot configure it. Declared source files must exist as regular files at the frozen baseline.

## 3. Start and transfer the handoff

From a clean baseline:

```sh
ekzd start .ekzd/local/task.toml
```

`start` validates the committed project policy and checkout, freezes the exact baseline/branch/authority/runtime identity, and atomically creates the usable archive:

```text
.ekzd/local/handoffs/<contract-id>/handoff.zip
```

Its output includes the contract ID, archive path, implementation branch, and worker next action. `ekzd status` shows the active archive path again if you need it.

The archive contains `instructions.md`, `contract.json`, `run.py`, `handoff.json`, and the exact pinned `runtime/ekzd/*.py` source. Transfer that archive and obtain the target repository separately. Extract the handoff outside the target checkout.

`ekzd handoff` is recovery/copy functionality only. It recreates the retained active archive or writes a byte-identical external copy:

```sh
ekzd handoff
ekzd handoff --output /outside/repository/handoff.zip
```

A normal task does not require a separate export step after `start`.

## 4. Worker flow

Start the target checkout from the frozen baseline on the declared implementation branch, then use only the transferred handoff runtime:

```sh
git switch -c feat/registration-validation <baseline-sha>

python3 /path/to/handoff/run.py prepare --json
python3 /path/to/handoff/run.py check --json

# Implement, test, fix, and self-review.
git add <scoped-files>
# Commit coherent, logically focused milestones; avoid unrelated batching and
# noisy WIP/checkpoint/fixup commits.
git commit -m "feat: validate registration"

python3 /path/to/handoff/run.py check --final --json
```

The worker does not need an installed EkzD package. The launcher isolates and verifies its bundled runtime before evaluation.

`prepare` requires the declared branch at the exact clean baseline and runs frozen readiness probes only. Development `check` permits dirty scoped work. `check --final` requires a clean committed candidate on the declared implementation branch. Every check repeats readiness before correctness verification.

PASS, FAIL, and UNAVAILABLE remain distinct. Required FAIL or UNAVAILABLE results block handoff readiness.

Use as many small, meaningful milestone commits as the task reasonably requires. The 50-commit limit is a safety ceiling, not an expected commit count. EkzD mechanically enforces commit-count and history rules without attempting to judge commit quality.

## 5. Maintainer verification and acceptance

After obtaining the exact worker candidate:

```sh
git switch feat/registration-validation
ekzd verify
# Review the exact verified candidate.
ekzd finish --accept
```

Maintainer verification is independent of worker claims and binds evidence to the frozen contract and exact candidate. If the candidate changes after verification, acceptance becomes stale and verification must be rerun.

The human retains merge authority.

To abandon an active task:

```sh
ekzd abort
```

Then edit task/policy inputs as needed and start again. Abort does not rewrite Git history or restore files.

## Status and commands

`ekzd status` is an advisory workflow compass. For an active task it shows the objective, implementation branch, frozen baseline, handoff path, candidate state, fixed commit ceiling, verification state, and the practical next action.

Supported commands:

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

Bare `ekzd` prints help without interaction. `NO_COLOR` disables ANSI styling. Worker `--json` output remains structured and stable.

Compatibility note: pre-1.0 legacy project/task/session formats and removed `contract`, `prompt`, `context`, old task-manifest, and installed worker-check interfaces are unsupported. Remove obsolete local state and refreeze with the current schemas; there is no migration layer.

## Trust model

EkzD preserves the exact baseline, implementation branch, history-aware scope, raw candidate binding, frozen contract authority, contract ID, pinned runtime identity, deterministic handoff integrity, repeated readiness, verifier mutation detection, process-group cleanup, bounded output, atomic session/handoff publication, stale-verification rejection, independent maintainer verification, and explicit human acceptance.

The contract ID and handoff hashes are integrity checksums, not signatures. EkzD assumes a trusted maintainer environment and a conventional Git checkout. It does not sandbox hostile same-user processes or prove semantic correctness.

See [VERIFICATION.md](VERIFICATION.md) for precise candidate evidence, frozen-authority composition, evaluator behavior, lifecycle binding, scope/history rules, and acceptance limits.

## Boundaries

EkzD deliberately does not provide:

- model/API integration;
- dependency or package installation;
- repository cloning;
- automatic commits, pushes, pull requests, merges, or deployments;
- daemon/background services;
- dashboards, TUIs, databases, plugins, or a policy DSL;
- Windows/macOS worker support;
- operating-system sandboxing.

Linux, Python 3.11+, Git, and project-declared capabilities are expected to be provisioned externally.

## Development

Install from the repository:

```sh
python3 -m pip install .
ekzd --help
ekzd --version
```

Run the test suite:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m compileall -q src tests
```
