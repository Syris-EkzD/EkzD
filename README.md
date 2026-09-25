# EkzD

EkzD is a deterministic structural trust layer for scoped AI-assisted work. It freezes durable
project policy plus disposable task intent into one contract, creates a portable pinned worker
handoff, and independently verifies that an exact Git candidate stays within that authority.

EkzD verifies authority and evidence. CI verifies project correctness. Human review retains merge
authority.

## Three-layer model

1. **EkzD** verifies the exact baseline, declared branch, contract/handoff/runtime identity,
   supported repository state, history-aware scope, fixed 50-commit safety ceiling, exact candidate
   binding, final cleanliness, stale evidence, and explicit acceptance.
2. **CI or equivalent external verification** runs project-specific tests, builds, formatting,
   linting, static analysis, dependency checks, and other machine-checkable correctness work.
3. **Maintainer and human review** decide semantic correctness, acceptance-criteria satisfaction,
   architecture, code quality, and whether to merge.

EkzD does not run project-specific commands, query CI providers, install project dependencies, push
branches, create pull requests, or merge.

## Workflow

```text
task freeze
→ handoff
→ worker implementation
→ iterative EkzD structural checks
→ clean committed candidate
→ EkzD final structural PASS
→ publish candidate
→ external CI
→ CI failure loops back to worker fixes, structural recheck, and CI
→ CI PASS on the exact candidate
→ maintainer EkzD verification
→ human/code review
→ explicit acceptance and separate merge decision
```

## 1. Initialize project policy

Run once inside a conventional Git checkout:

```sh
ekzd init
```

The generated closed-schema `.ekzd/project.toml` is structural policy only:

```toml
schema_version = 4
name = "Example"
protected = []
exclude = []
guidance = []
```

Project fields are `schema_version = 4`, non-empty `name`, and optional string arrays for
`protected`, `exclude`, and `guidance`. `verification` and `readiness` are not accepted fields.

Commit the policy and generated ignore rule:

```sh
git add .ekzd/project.toml .gitignore
git commit -m "chore: configure EkzD policy"
```

`.ekzd/local/` remains ignored and untracked.

## 2. Write a disposable task

Keep task drafts under the ignored local directory, for example `.ekzd/local/task.toml`:

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

Required task fields are `schema_version`, `objective`, `branch`, non-empty `include`, and non-empty
`acceptance`. Optional fields are `exclude`, `sources`, and `guidance`. `verification` and
`readiness` are not accepted fields.

Task exclusions extend project exclusions. Project protections cannot be removed by a task.
Guidance is additive. Declared source files must exist as regular files at the frozen baseline. The
fixed 50-commit safety ceiling is part of every frozen contract and is not author-configurable.

## 3. Freeze and transfer the handoff

From a clean baseline:

```sh
ekzd start .ekzd/local/task.toml
```

`start` validates the committed project policy and checkout, then freezes contract schema v3 with:

- exact baseline and implementation branch;
- project name and normalized policy digest;
- objective, resolved include/exclude/protected scope, sources, acceptance, and guidance;
- fixed 50-commit safety ceiling; and
- exact EkzD version/build identity.

The contract contains no project command plan. `start` atomically creates:

```text
.ekzd/local/handoffs/<contract-id>/handoff.zip
```

The archive contains `instructions.md`, `contract.json`, `run.py`, `handoff.json`, and the exact
pinned `runtime/ekzd/*.py` source. Transfer it and obtain the repository separately. Extract the
handoff outside the checkout. `ekzd handoff` only recreates the retained archive or writes a
byte-identical external copy.

## 4. Worker structural flow and external CI

```sh
git switch -c feat/registration-validation <baseline-sha>

python3 /path/to/handoff/run.py prepare --json
python3 /path/to/handoff/run.py check --json

# Implement and validate project correctness with the externally provided workflow.
# Make coherent, logically focused commits within the frozen scope.

python3 /path/to/handoff/run.py check --final --json
```

The worker needs only Linux, Python 3.11+, Git, the repository, and the portable handoff for EkzD
itself. External CI must provision its own project-specific dependencies and toolchains.

- `prepare` verifies the pinned runtime/handoff/contract, Linux/Python/Git prerequisites, supported
  repository state, local frozen baseline, declared branch, and exact clean baseline state.
- Development `check` verifies current structural authority while allowing legitimate dirty
  in-scope work.
- `check --final` requires a clean committed candidate and verifies complete structural authority
  for that exact Git-visible state.

A structural PASS means only: **this exact candidate complies with the frozen EkzD authority.** It
does not imply that the code builds, tests pass, lint or formatting passes, dependencies are valid,
or acceptance criteria are semantically satisfied.

After final structural PASS, publish the exact candidate through the provided external workflow and
wait for required CI. Do not report successful completion while CI is pending or failing. When CI
fails, fix within the frozen scope, make a coherent commit, rerun final structural checking, publish
the new candidate, and wait again. Stop for a human decision if a fix requires wider authority,
protected paths, out-of-task CI configuration, unavailable infrastructure, or requirement
interpretation. The worker must not merge.

Commit discipline and the full return requirements are embedded deterministically in
`instructions.md`. EkzD enforces commit count and history, but never grades commit-message quality or
semantic meaningfulness.

## 5. Maintainer verification and acceptance

After required external CI passes the exact worker candidate:

```sh
git switch feat/registration-validation
ekzd verify
# Review the exact candidate and its external CI evidence.
ekzd finish --accept
```

`ekzd verify` independently repeats structural/candidate verification against the maintainer's
frozen authority. It does not run project commands or query CI. If candidate or contract identity
changes, verification becomes stale. `finish --accept` requires fresh successful structural evidence
bound to the same exact candidate and contract. The human retains the separate merge decision.

Use `ekzd abort` to close an active task without acceptance. Abort does not rewrite Git history or
restore files.

## Commands and compatibility

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

Bare `ekzd` prints help without interaction. `NO_COLOR` disables ANSI styling. Worker `--json`
output is structured. Pre-1.0 project/task/contract/session formats are unsupported; remove obsolete
local state and refreeze. There is no migration layer.

## Trust model and boundaries

EkzD preserves exact structural authority and evidence: baseline ancestry, branch, history-aware
scope, candidate binding, contract/handoff/runtime identity, deterministic handoff integrity,
unsupported-state rejection, final cleanliness, stale-verification rejection, independent
maintainer verification, and explicit human acceptance.

Contract IDs and handoff hashes are integrity checksums, not signatures. EkzD assumes a trusted
maintainer environment and conventional quiescent Git checkout. It is not an operating-system
sandbox and does not prove project or semantic correctness.

EkzD deliberately provides no model/API integration, CI-provider integration, network
orchestration, dependency installers, framework detection, repository cloning, automatic pushes,
pull requests, merges, deployments, daemons, dashboards, databases, or Windows/macOS worker support.

See [VERIFICATION.md](VERIFICATION.md) for precise evidence and acceptance semantics.

## Development

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m compileall -q src tests
git diff --check
```

These are contributor commands run outside EkzD; they are not frozen or executed by EkzD.
