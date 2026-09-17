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

Only `schema_version`, `objective`, `branch`, non-empty `include`, and non-empty `acceptance` are required. Task budget overrides the project default. Project exclusions and task exclusions are combined; project protections cannot be removed by a task. Guidance and verification steps are additive. Sources must be regular files at the frozen baseline and may later be modified, renamed or deleted if scope permits.

From a clean baseline:

```sh
ekzd start .ekzd/local/task.toml
ekzd contract > .ekzd/local/contract.json
ekzd prompt > .ekzd/local/prompt.txt
```

Start validates committed project policy and the clean supported checkout, freezes one effective contract, and prints the declared implementation branch. It does not create or switch branches. The implementation branch must differ from the branch used to freeze the baseline; a detached baseline is allowed.

Transfer the JSON contract and prompt to the worker along with access to the baseline repository and exact pinned EkzD build. **This remains a manual handoff until Phase 4.** `contract` and `prompt` are temporary plain-output interfaces backed by the same frozen authority; they do not assemble archives or install a runtime/toolchain. Keep exported contracts outside tracked candidate files (for example under `.ekzd/local/`).

Worker steps:

```sh
# Start from the frozen baseline commit, on the declared branch.
git switch -c feat/registration-validation <baseline-sha>
ekzd check --contract /path/to/contract.json --json
# Implement, check, fix, self-review.
git add <scoped-files>
git commit -m "feat: validate registration"
ekzd check --contract /path/to/contract.json --final --json
# Return the exact committed candidate and check results to the maintainer.
```

The initial development check must succeed before claiming readiness; missing required verification tooling is a hard stop. Development checks allow dirty, scoped work. Final checks require a clean, fully committed candidate on the declared branch. Both use the same frozen-contract checker and command evaluator as maintainer verification. Worker checking does not read or modify the maintainer session.

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

The effective JSON contract contains `contract_version = 1`, baseline commit, implementation branch, project name and configuration digest, objective, resolved scope (`include`, `exclude`, `protected`), sources, acceptance, guidance, budget, complete verification steps and exact EkzD version/build identity. It contains no timestamp, output location, remote URL or self-referential ID.

Canonical bytes use UTF-8, sorted object keys, compact `,` / `:` separators, no ASCII escaping of Unicode, and exactly one final LF. Scope lists are sorted and deduplicated; guidance, acceptance and verification retain their declared order. Project configuration digest hashes its normalized parsed representation, excluding TOML formatting noise. `contract_id = SHA256(canonical_contract_bytes)` is an integrity checksum, **not a signature or authentication mechanism**. Worker JSON must be canonical; export it rather than hand-editing it.

After start, editing the original task file or current project TOML does not change frozen authority. Verification never reconstructs it from those mutable inputs. Editing project policy still violates its protected candidate path. Changing authority requires abort/refreeze. The frozen contract and its ID are trusted local operator metadata; a hostile same-user process that rewrites both is outside the threat model.

Both worker checks and maintainer verification/acceptance require the frozen EkzD version and build fingerprint. `ekzd --version` reports that identity: sorted package-relative `.py` paths and normalized source bytes, independent of installation location, Git metadata and caches. An unavailable or mismatching build blocks checking. Runtime delivery and environment provisioning remain external responsibilities. Repository remotes never authorize a task; changing HTTPS/SSH transports does not invalidate it.

## Commands and compatibility

```sh
ekzd --help
ekzd --version
ekzd init
ekzd start path/to/task.toml
ekzd status
ekzd contract
ekzd prompt
ekzd check --contract path/to/contract.json [--final] [--json]
ekzd verify
ekzd finish --accept
ekzd abort
```

`status` is advisory guidance (`not run`, `passed`, `stale`, `failed`, `blocked`); verification and acceptance enforce their own checks. Bare `ekzd` prints help without interaction. Human output retains the existing terminal color behavior (`NO_COLOR` disables it); JSON and exported contract/prompt output are plain.

This pre-1.0 change deliberately removes old project-task conflation, task-manifest schemas v1/v2, old session formats, `start "objective" --branch ...`, `task`, `context`, `check --task`, repository-locator gates and the universal 20-commit ceiling. Remove an old local session and start a new task with the current schemas. There is no automatic migration.

## Acceptance model

`ekzd verify` and worker `ekzd check --final` require a clean, fully committed candidate on the declared implementation branch. Detached candidates, unresolved conflicts and in-progress Git operations are rejected. `ekzd check` permits dirty development work.

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
