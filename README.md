# EkzD

EkzD is a small, project-agnostic development harness for scoped and verifiable AI-assisted work.

It does not run an AI model, orchestrate agents, replace Git, or replace CI. Its job is narrower: define the working contract for a task, expose that contract to the tools or people doing the work, keep each session bounded, run deterministic local verification, and refuse acceptance unless the verified state still matches the state being accepted.

## Project setup and repeated tasks

Configure `.ekzd/project.toml` once with durable project policy, then commit it and the local-state ignore rule:

```sh
ekzd init
# Edit .ekzd/project.toml: permanent protections, guidance, required checks.
git add .ekzd/project.toml .gitignore
git commit -m "chore: configure EkzD policy"
mkdir -p .ekzd/local
```

The supported project schema is shallow TOML:

```toml
schema_version = 2
name = "Example"
protected = ["secrets/", ".github/"]
exclude = ["src/generated/"]
guidance = ["Keep dependencies minimal."]
max_commits = 20

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

`verification` must contain at least one required step. Remove the empty `verification = []` placeholder created by `init` when adding `[[verification]]` tables. Steps execute direct argv arrays without a shell. `cwd` defaults to `.` and timeout defaults to 600 seconds (positive integer, at most 3600). `max_commits` defaults to 20, but any positive integer is supported. Other project lists default to empty. Unknown keys are errors in every supported schema/object.

For each task, write disposable `.ekzd/local/task.toml`:

```toml
schema_version = 1
objective = "Add registration validation"
branch = "feat/registration-validation"
include = ["src/registration/", "tests/registration/"]
exclude = []
sources = ["src/registration/model.py"]
acceptance = ["Invalid usernames are rejected.", "Existing valid usernames still pass."]
guidance = ["Keep this implementation dependency-free."]
max_commits = 8

# Optional additive checks; project checks always run first.
[[verification]]
name = "focused-tests"
command = ["python3", "-m", "unittest", "tests.registration.test_validation"]
```

Only `schema_version`, `objective`, `branch`, non-empty `include`, and non-empty `acceptance` are required. Task budget overrides the project default. Project exclusions and task exclusions are combined; project protections cannot be removed by a task. Guidance, readiness probes and verification steps are additive. Optional `[[readiness]]` tables use the same name/argv/cwd/timeout shape in both project and task files. Probes should check capabilities (for example an executable version or module import), not run the full correctness suite. Sources must be regular files at the frozen baseline and may later be modified, renamed or deleted if scope permits.

From a clean baseline:

```sh
ekzd start .ekzd/local/task.toml
# Output includes contract ID and .ekzd/local/handoffs/<contract-id>/handoff.zip.
```

Start validates committed project policy and the clean supported checkout, freezes one effective contract, and prints the declared implementation branch. It does not create or switch branches. The implementation branch must differ from the branch used to freeze the baseline; a detached baseline is allowed.

Start captures the executing Python package and automatically publishes a complete portable ZIP with
`instructions.md`, `contract.json`, `run.py`, `handoff.json` and `runtime/ekzd/*.py`.
Transfer this archive and obtain the target repository separately. Extract the archive outside the
checkout; the worker needs Linux, Python 3.11+, Git and the declared project capabilities, but **no
installed EkzD** and no network access to obtain EkzD. EkzD installs nothing and does not fetch Git.

`ekzd handoff` recreates a missing active archive from retained payload. Use
`ekzd handoff --output /outside/repository/handoff.zip` for a copy. Re-export is byte-identical and
uses neither current project/task inputs nor a newer installed runtime. Different existing output
is never overwritten. Retained payload is under `.ekzd/local/handoffs/<contract-id>/payload/`.

Worker steps:

```sh
# Start from the frozen baseline commit, on the declared branch.
git switch -c feat/registration-validation <baseline-sha>
python3 /path/to/handoff/run.py prepare --json
python3 /path/to/handoff/run.py check --json
# Implement, check, fix, self-review.
git add <scoped-files>
git commit -m "feat: validate registration"
python3 /path/to/handoff/run.py check --final --json
# Return the exact committed candidate and check results to the maintainer.
```

Preparation requires the declared branch at the exact clean baseline, validates the supported checkout and handoff identity, and runs only readiness probes. Missing executables/modules or failing capability probes block preparation as `UNAVAILABLE`. Every later check repeats readiness before correctness verification; no readiness token is cached. Task tests may refer to files that do not exist until implementation. Probes are trusted read-only commands: persistent candidate mutation fails closed, but EkzD is not a sandbox. Development checks allow dirty, scoped work. Final checks require a clean, fully committed candidate on the declared branch. Both use the same frozen-contract checker and command evaluator as maintainer verification. Worker checking does not read or modify the maintainer session.

Maintainer steps, after obtaining and reviewing the worker candidate:

```sh
git switch feat/registration-validation
ekzd verify
# Review the exact verified candidate.
ekzd finish --accept
# Human retains merge authority.
```

Write the next local task and run `ekzd start .ekzd/local/task-b.toml`. No project-policy edit or administrative commit is needed. To change an active task's authority, run `ekzd abort`, edit inputs, and start again. Abort does not restore files or rewrite Git history. A new task never inherits previous verification evidence.

## Frozen authority and local state

`.ekzd/local/` is ignored and must remain untracked. `.ekzd/project.toml` remains committed policy. One atomic `.ekzd/local/session.json` record contains the effective contract, its `contract_id`, lifecycle status and verification/acceptance evidence. No database is used; Linux advisory locking prevents competing lifecycle commands.

The effective JSON contract contains `contract_version = 2`, baseline commit, implementation branch, project name and configuration digest, objective, resolved scope (`include`, `exclude`, `protected`), sources, acceptance, guidance, budget, complete readiness/verification steps and exact EkzD version/build identity. It contains no timestamp, output location, remote URL or self-referential ID.

Canonical bytes use UTF-8, sorted object keys, compact `,` / `:` separators, no ASCII escaping of Unicode, and exactly one final LF. Scope lists are sorted and deduplicated; guidance, acceptance and verification retain their declared order. Project configuration digest hashes its normalized parsed representation, excluding TOML formatting noise. `contract_id = SHA256(canonical_contract_bytes)` is an integrity checksum, **not a signature or authentication mechanism**. Worker JSON must be canonical; use the generated handoff without editing its contents.

After start, editing the original task file or current project TOML does not change frozen authority. Verification never reconstructs it from those mutable inputs. Editing project policy still violates its protected candidate path. Changing authority requires abort/refreeze. The frozen contract and its ID are trusted local operator metadata; a hostile same-user process that rewrites both is outside the threat model.

Both worker checks and maintainer verification/acceptance require the frozen EkzD version and build fingerprint. `ekzd --version` reports that identity: sorted package-relative `.py` paths and normalized source bytes, independent of installation location, Git metadata and caches. An unavailable or mismatching build blocks checking. The handoff delivers the pinned runtime; target toolchain/environment provisioning remains external. Repository remotes never authorize a task; changing HTTPS/SSH transports does not invalidate it.

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

`status` is advisory guidance (`not run`, `passed`, `stale`, `failed`, `blocked`); verification and acceptance enforce their own checks. Bare `ekzd` prints help without interaction. Human output retains the existing terminal color behavior (`NO_COLOR` disables it); worker `--json` output and the handoff path are plain.

Phase 4 removes temporary `contract`, `prompt` and installed `check --contract` commands. Contract version 1 and earlier local sessions are unsupported; remove the old local session and refreeze.

The earlier pre-1.0 change deliberately removes old project-task conflation, task-manifest schemas v1/v2, old session formats, `start "objective" --branch ...`, `task`, `context`, `check --task`, repository-locator gates and the universal 20-commit ceiling. Remove an old local session and start a new task with the current schemas. There is no automatic migration.

## Acceptance model

`ekzd verify` and worker `python3 run.py check --final` require a clean, fully committed candidate on the declared implementation branch. Detached candidates, unresolved conflicts and in-progress Git operations are rejected. `python3 run.py check` permits dirty development work.

Candidate binding hashes raw tracked and non-ignored untracked file contents, Git executable modes, symlink values, HEAD, branch, and index identities. It does not use textconv or external diff output. Raw working files must match their index blobs for final readiness; a checkout transformation such as LF-to-CRLF conversion is therefore not a clean final candidate even if ordinary Git status hides it. Verification captures the candidate before commands, checks it before and after each command and at completion, and rejects observed mutation without restoring it or adopting it as verified state.

Submodules (clean or dirty), shallow repositories, sparse checkout, hidden index flags, active tracked content filters, replacement refs/grafts, nested repositories encountered as candidate paths, and special candidate files are unsupported. Complete Git operations and use a conventional checkout before evaluating. Snapshots assume a quiescent checkout; they are not a sandbox or an atomic filesystem transaction. Ignored files and external symlink-target contents are not bound. A command checking an ignored file does not add that file to the fingerprint.

`verify` clears previous success before a new attempt, checks frozen scope/protections/budget, and runs the resolved required steps. A contract/session change during execution stops later checks. Verification evidence binds the initial candidate and exact contract ID. Ordinary nonzero failures can continue while trust remains intact; `FAIL` and `UNAVAILABLE` always block readiness. Verifier stdout and stderr are bounded and invalid UTF-8 is replaced.

`finish --accept` requires successful maintainer verification for the same contract ID, HEAD, branch and raw candidate fingerprint. It rechecks final state, scope, protections, budget and runtime identity without rerunning the commands. The accepted record identifies that exact candidate and contract. A stale candidate or contract requires new verification.

Protected paths include `.ekzd/project.toml`, `.ekzd/local/`, the legacy `.ekzd/session.json`, and project-specific protections. They cannot be changed in candidate history, even if later restored or untracked. Ignored local session evidence is bound separately while verification runs.

`--accept` is an explicit operator attestation, not proof of the operator's identity or semantic correctness. See [VERIFICATION.md](VERIFICATION.md) for execution behavior and trust limits.

## Boundaries

The current design deliberately stays small:

- single-operator personal workflow rather than multi-user configuration ownership;
- conventional Git checkout only: no custom tracked-file content filters or Git LFS, no `assume-unchanged`, and no `skip-worktree`/sparse-checkout state;
- no daemon;
- no database;
- no model/API integration;
- no network operations;
- no GitHub authentication inside EkzD;
- no autonomous PR creation, commits, pushes, merges, or deployments;
- no real-time interception of an AI's GitHub writes;
- no operating-system sandbox against a hostile same-user process with arbitrary filesystem, Git, or process control;
- no claim that free-text authority rules are mechanically understood;
- no claim that deterministic checks can prove semantic correctness.

GitHub Actions can provide remote verification for repositories that need dependencies or services unavailable in a local/session environment. EkzD remains the harness; CI remains an independent execution/verification service.

## Development

EkzD requires Python 3.11+ and Git.

Install it from the repository with:

```sh
python3 -m pip install .
ekzd --help
ekzd --version
```

Run its tests with:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m compileall -q src tests
```

The repository history before EkzD is retained as the historical WhiteTree Workflow Manager experiment. The current implementation replaces that mechanism rather than extending it.
