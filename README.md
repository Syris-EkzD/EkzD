# EkzD

EkzD is a small, project-agnostic development harness for scoped and verifiable AI-assisted work.

It does not run an AI model, orchestrate agents, replace Git, or replace CI. Its job is narrower: define the working contract for a task, expose that contract to the tools or people doing the work, keep each session bounded, run deterministic local verification, and refuse acceptance unless the verified state still matches the state being accepted.

EkzD is deliberately not the code generator. In the intended V1.1 workflow, an AI coding session, Codex, or a human produces the changes; EkzD provides the frozen contract, deterministic semantic and machine-readable handoffs, workflow guidance, and acceptance gate around that work. The harness remains optimized for a single-operator personal workflow rather than a multi-user policy platform.

## Recommended V1.1 workflow

V1.1 keeps planning/maintenance and implementation as separate responsibilities without requiring the operator to manually translate EkzD session state into handoff artifacts.

```text
You + maintainer session define the objective
        ↓
configure + commit .ekzd/project.toml
        ↓
ekzd start "..." --branch feat/example-task
        ↓
ekzd status
        ↓
ekzd prompt
        +
ekzd task --output /outside/project/task.toml
        ↓
Implementation session
        ↓
consume semantic prompt + schema-v2 worker task
        ↓
create/use declared task branch from frozen baseline
        ↓
implement → test → fix → self-review
        ↓
push/update branch + open/update PR when supported
        ↓
GitHub CI
        ↓
you pull the returned task branch locally
        ↓
ekzd status
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
- **Maintainer/planning session** helps define and review the task but does not need to manually rewrite the frozen contract for the implementation session.
- **EkzD** freezes the local session contract and portable workflow metadata, renders the deterministic semantic prompt and schema-v2 worker-task manifest, shows the current workflow state, and checks the Git-visible result against that contract.
- **Implementation session** consumes the semantic handoff and worker-task authority, works on the declared task branch, produces and tests the actual repository changes, and opens or updates a PR when its environment supports those operations.
- **GitHub** carries branches, commits, pull requests, and collaboration history.
- **CI** independently executes remote checks where appropriate.

The active session state lives in `.ekzd/session.json`, which is intentionally local and gitignored. EkzD requires that path to remain untracked and also rejects an active session if `.ekzd/session.json` appears in any commit made during that session, even when a later commit removes it from tracking again. An implementation session operating only through GitHub therefore cannot legitimately replace or publish that local contract through accepted repository history.

`ekzd start` requires an explicit implementation/task branch. The session records the baseline branch and HEAD separately from that declared implementation branch, so starting from `main` does not authorize implementation directly on `main`. A detached baseline is represented as detached, but it still requires a real declared task branch for the portable handoff.

At session start EkzD also captures a sanitized repository identifier derived from `remote.origin.url` when one exists. Userinfo, credentials, query parameters, and fragments are excluded. That identifier is frozen in local session metadata, so later changes or removal of `origin` do not silently change the implementation handoff. If no origin exists at session start, EkzD records that fact instead of inventing a remote locator.

`ekzd prompt` is the semantic V1.1 bridge between the local trusted session and a separate implementation environment. It renders the frozen objective, repository baseline, baseline branch, implementation branch, repository identity metadata, sources, scope, authority, acceptance criteria, verification plan, and session budget into an environment-agnostic implementation handoff. It explicitly distinguishes environments with a local Git checkout from environments that only have repository/API access. It also instructs the implementation session to use logical commits, run/fix/retest available checks, self-review, push/update the declared branch and PR when supported, never merge the PR, and report limitations when repository operations are unavailable.

`ekzd task` is the complementary machine-enforced handoff. It exports a canonical task schema-v2 TOML manifest from the same active trusted session, carrying only authority that current worker checking enforces: objective, frozen baseline HEAD, declared implementation branch, commit ceiling, scope include/exclude lists, acceptance criteria, optional frozen repository identifier, and the exact executing EkzD version/build identity. It deliberately does not copy project verification commands into task verification, and it does not export declared sources, prose constraints, or human authority lists as unenforced task keys. `ekzd task` writes only the manifest to stdout; `ekzd task --output <path>` writes the exact same bytes to a path that resolves outside the project root. Existing identical output is accepted idempotently, while different files, symlinks, non-regular destinations, unsafe in-project destinations, and partial-write situations fail closed.

`ekzd context --json` remains available as the lower-level structured contract for tools that need machine-readable data. EkzD still does not authenticate to GitHub, create PRs itself, or control an AI's GitHub actions in real time. Instead, it validates the resulting Git state after that work is returned to the environment where the trusted EkzD session is running.

## Commands

```sh
ekzd
ekzd --version
ekzd init
ekzd start "implement registration validation" --branch feat/registration-validation
ekzd status
ekzd prompt
ekzd task
ekzd task --output /outside/project/task.toml
ekzd context
ekzd context --json
ekzd verify
ekzd check --task /path/to/task.toml
ekzd abort
ekzd finish --accept
```

Running bare `ekzd` is deterministic and non-interactive: it prints the ordinary command-line help and exits successfully. Task lifecycle actions are explicit subcommands; EkzD does not open a menu or TUI.

`ekzd --version` reports the semantic package version together with the exact 64-character lowercase SHA-256 of the Python source files in the executing `ekzd` package. The build fingerprint is based only on sorted package-relative `.py` paths plus normalized source bytes (CRLF and lone CR are normalized to LF); it does not depend on Git metadata, installation paths, bytecode/cache files, or installer metadata. If EkzD cannot determine that exact source identity safely, the command exits non-zero instead of printing a placeholder build value.

`ekzd status` is the operator-facing workflow compass. For an active session it shows the objective, baseline branch and HEAD, declared implementation branch, current branch, worktree state, commit budget, verification state, and the next expected action. A newly started unchanged session points to both `ekzd prompt` and `ekzd task` as complementary handoff artifacts. Its verification state is one of the workflow-facing states `not run`, `passed`, `stale`, `failed`, or `blocked` as appropriate. A previously successful verification becomes `stale` if the exact supported Git-visible state changes. An exceeded commit budget or history that no longer descends from the frozen baseline is shown as `blocked` instead of suggesting verification can continue normally.

## Stateless worker checking

`ekzd check` is a separate worker-side path for implementation environments that should validate a frozen task without owning EkzD's local session lifecycle. Unlike `start`, `verify`, and `finish`, worker checking does not require, read, or update `.ekzd/session.json`. The existing session workflow remains the maintainer/operator acceptance path.

Worker authority comes from a standalone TOML task manifest. Schema version 1 remains accepted for compatibility, while schema version 2 adds an exact EkzD harness identity pin. For a trusted active maintainer session, `ekzd task` is the deterministic way to export the schema-v2 worker manifest instead of manually transcribing it. The file may live outside the repository, and every result identifies the exact manifest bytes with a SHA-256 digest. Repository-level verification is still trusted from `.ekzd/project.toml` at the manifest's frozen baseline commit; optional task verification steps are additive and cannot replace those project checks.

A schema-v1 task remains valid and intentionally does not pin the EkzD harness build:

```toml
schema_version = 1
objective = "Implement registration validation"
baseline = "0123456789abcdef0123456789abcdef01234567"
implementation_branch = "feat/registration-validation"
max_commits = 4
# Optional: sanitized repository identity expected from remote.origin.url.
repository = "https://github.com/example/project"

[scope]
include = ["src/", "tests/"]
exclude = ["src/generated/"]

[acceptance]
criteria = ["Validation behavior is covered by tests."]

# Optional additive checks.
[[verification.steps]]
name = "focused-tests"
command = ["python3", "-m", "unittest", "tests.test_validation"]
cwd = "."
timeout_seconds = 120
```

Schema-v1 checks emit a non-blocking warning that the harness build is unpinned. To pin the worker to one exact EkzD build, use schema version 2 and add the required `[ekzd]` table:

```toml
schema_version = 2
objective = "Implement registration validation"
baseline = "0123456789abcdef0123456789abcdef01234567"
implementation_branch = "feat/registration-validation"
max_commits = 4

[ekzd]
version = "0.3.0"
build_sha256 = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"

[scope]
include = ["src/", "tests/"]
exclude = []

[acceptance]
criteria = ["Validation behavior is covered by tests."]
```

For schema v2, both the semantic version and build SHA-256 must match the executing EkzD identity before any project or task verification command runs. A mismatch is blocking. If the executing build identity cannot be determined reliably, worker checking fails closed and cannot report readiness. Task export follows the same fail-closed identity rule: if the executing build cannot be fingerprinted exactly, no usable worker manifest is emitted. Structured worker results include the executing EkzD `version` and `build_sha256` when available, and human-readable `ekzd check` output displays the same identity.

During implementation, run a development check against the current HEAD plus staged, unstaged, and untracked non-ignored work:

```sh
ekzd check --task /path/to/task.toml
ekzd check --task /path/to/task.toml --json
```

Before handoff, run final checking:

```sh
ekzd check --final --task /path/to/task.toml
```

Final checking requires a clean worktree and binds a successful result to the exact final HEAD. Both modes verify baseline ancestry, the declared implementation branch, the commit ceiling, protected EkzD paths and supported Git visibility, task scope, optional repository identity, and all baseline project plus additive task verification commands. Worker checking and maintainer verification use the same command evaluator and candidate-mutation handling. Verification commands are executed directly as argv arrays, never through a shell. On Linux each command runs in its own process group; on timeout or keyboard interruption EkzD cleans up the owned group before recapturing candidate state. This does not contain deliberately daemonized descendants that escape the process group. EkzD does not repair, format, stage, commit, reset, restore, or otherwise rewrite the candidate; if a configured verifier changes Git-visible project state, the check fails and leaves that mutation visible for inspection.

Each individual result uses one of four states: `PASS` means the check ran and succeeded; `FAIL` means it ran and found a blocking violation; `UNAVAILABLE` means a required check could not be evaluated or executed and is blocking; `WARN` is a non-blocking observation. Verifier stdout and stderr are retained with a fixed bound, invalid UTF-8 is decoded with replacement characters, and truncated output is marked in structured results. Overall readiness succeeds only when no required result is `FAIL` or `UNAVAILABLE`. Ordinary independent failures are aggregated where safe so one failed command does not hide unrelated results.

If an active session cannot or should not be accepted, close it without acceptance:

```sh
ekzd abort
```

`abort` changes only local EkzD session state. It does not revert project files, commits, or Git history, and it never records acceptance.

`ekzd init` creates `.ekzd/project.toml` and ignores `.ekzd/session.json`. Before `ekzd start`, configure and commit `.ekzd/project.toml`. V1.1 also requires the working tree to be clean when starting through the CLI so the recorded task baseline can be reproduced by a separate development environment. Session state remains local and disposable.

## CLI output

Human-readable explicit commands use semantic color when standard output is an interactive terminal. Color is disabled for redirected or piped output, when `TERM=dumb`, or when the standard `NO_COLOR` environment variable is present. `ekzd context --json` always remains machine-readable JSON without ANSI styling, and `ekzd prompt` remains deterministic plain text suitable for direct handoff. `ekzd task` is stricter: stdout mode emits only canonical schema-v2 TOML with exactly one trailing newline and no presentation wrapper, while `--output` writes those exact UTF-8/LF bytes to the requested safe external file and is otherwise silent on success.

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
max_commits = 20

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

Configuration is intentionally explicit. Empty `scope.include`, empty acceptance criteria, or an empty verification plan prevent a session from starting. Unknown keys in supported configuration and task-manifest tables are rejected instead of ignored, so misspelled policy fields do not silently weaken the contract. A deliberately repo-wide scope must still be explicit, for example `include = ["*"]`.

`.ekzd/project.toml` is the canonical committed project contract. `ekzd start` refuses to begin a CLI session unless that file already exists in `HEAD` as a regular tracked file, has no staged or unstaged changes, and the repository working tree is otherwise clean. The clean-start rule gives a separate implementation environment a reproducible commit baseline instead of depending on local-only edits that cannot be represented by the recorded HEAD.

The `--branch` value supplied to `ekzd start` is portable workflow metadata, not a replacement for the canonical project contract. EkzD validates it as a Git branch name, refuses to reuse the non-detached baseline branch as the implementation branch, and stores it in the local active session along with the frozen sanitized repository identity.

For an active session, EkzD parses and hashes the raw `HEAD:.ekzd/project.toml` blob rather than the worktree copy, so ordinary checkout transformations such as line-ending conversion cannot redefine the contract. `ekzd prompt`, `ekzd task`, `ekzd context`, `ekzd verify`, and `ekzd finish --accept` all remain bound to that committed contract while continuing to require the worktree/index config path to remain clean.

V1 deliberately rejects any tracked path with an active Git `filter` attribute. Clean/smudge filters can make Git's diff representation differ from the actual worktree bytes that verification commands execute, weakening scope and exact-state guarantees. This means Git LFS and repositories that rely on custom tracked-file filters are not supported by EkzD V1. Remove those filter attributes or use a conventional checkout before starting or verifying a session.

Changing scope, authority, sources, session policy, acceptance criteria, verification commands, or any other project harness configuration therefore requires ending the current session first. Abort or finish the session, edit and commit `.ekzd/project.toml`, then start a fresh session. Any active-session commit that touches `.ekzd/project.toml` permanently invalidates that session even if a later commit restores the exact original bytes.

The two V1 protected harness-history paths are exactly `.ekzd/project.toml` and `.ekzd/session.json`. Their protection is independent of `scope.include` and `scope.exclude`; even `include = ["*"]` cannot authorize committing either path during an active session. This is intentionally narrower than protecting the entire `.ekzd/` directory so future non-trust-critical EkzD artifacts can evolve without inheriting an unnecessary blanket restriction.

`session.max_commits` is a hard session-size safety ceiling. `ekzd init` writes `20`, and a missing value also resolves to `20`. Projects may configure a smaller positive integer, but values above `20` are rejected rather than clamped. The configured value is captured when the session starts; it cannot be raised mid-session to excuse work that has already exceeded its original boundary.

The ceiling is not a target. Implementation work should prefer small, logically isolated commits when multiple commits improve reviewability, without creating meaningless micro-commits. Unrelated changes should not be combined merely to reduce commit count. If a task approaches its configured ceiling, stop and reassess or report that the task may be exceeding its intended scope rather than creating oversized commits to stay under the limit.

## Acceptance model

Phase 1 final verification (`ekzd verify` and worker `ekzd check --final`) requires a clean, fully committed candidate on the declared implementation branch. Detached HEAD, unresolved conflicts, and in-progress Git operations are rejected. Use worker `ekzd check` for dirty development checks. Legacy sessions without a declared branch must be restarted; verification evidence from before candidate binding must be regenerated.

Candidate binding hashes raw tracked and non-ignored untracked file contents, Git executable modes, symlink values, HEAD, branch, and index identities. It does not use textconv or external diff output. Raw working files must match their index blobs for final readiness; a checkout transformation such as LF-to-CRLF conversion is therefore not a clean final candidate even if ordinary Git status hides it. Verification captures the candidate before commands, checks it before and after each command and at completion, and rejects observed mutation without restoring it or adopting it as verified state.

Submodules (clean or dirty), shallow repositories, sparse checkout, hidden index flags, active tracked content filters, replacement refs/grafts, nested repositories encountered as candidate paths, and special candidate files are unsupported. Complete Git operations and use a conventional checkout before evaluating. Snapshots assume a quiescent checkout; they are not a sandbox or an atomic filesystem transaction. Ignored files and external symlink-target contents are not bound. A command checking an ignored file does not add that file to the fingerprint.

EkzD treats verification and acceptance as different stages.

`ekzd verify`:

1. validates the committed project configuration;
2. requires `.ekzd/project.toml` to remain the clean regular tracked contract captured when the session started;
3. requires the active session state to remain local and untracked;
4. rejects Git visibility features unsupported by V1, including `assume-unchanged`, `skip-worktree`, and tracked-file `filter` attributes;
5. enforces the active session's commit budget;
6. rejects any active-session commit history that touches `.ekzd/project.toml` or `.ekzd/session.json`, independently of project scope;
7. checks changed paths against `scope.include` and `scope.exclude` before verification;
8. runs verification commands directly as configured argv arrays, without a shell;
9. reloads the committed project configuration from `HEAD`, rejects any verifier-time session-state change, and rechecks the clean frozen configuration, commit budget, protected harness history, supported Git visibility, and path scope against the original pinned session contract;
10. records the exact supported Git-visible state, final session budget, and committed configuration digest that passed.

`ekzd finish --accept` succeeds only when:

- verification passed for the same clean, committed candidate on the declared implementation branch;
- `.ekzd/project.toml` remains committed, clean, regular, and identical to the active-session and verified committed configuration;
- the local session state remains untracked;
- unsupported Git visibility features such as hidden index flags or tracked-file filters are absent;
- the session remains within its original commit budget;
- neither protected harness path appears in the active session's commit history;
- the Git HEAD, branch, worktree state, and supported Git-visible file contents still match the verified state;
- scope rules still pass; and
- the operator explicitly supplies `--accept` after the required review.

`--accept` is an operator attestation. V1 does not authenticate who typed the command or cryptographically prove that a particular human performed the review.

Acceptance requires the current supported Git-visible state to exactly match the verified state. If the current state differs, verification must be rerun before acceptance can succeed. `ekzd status` mirrors that rule by reporting the previous verification as `stale` rather than `passed` when the current exact state no longer matches.

The exact-state fingerprint covers tracked, staged, unstaged, and untracked state under V1's supported conventional Git checkout model. Git-ignored files are outside the fingerprint even when verification commands inspect them. Tracked files using content filters are rejected rather than fingerprinted through a potentially transformed Git view. `.ekzd/session.json` is handled separately as trusted local harness metadata: it must stay untracked, must not change while verification commands run, and must never enter active-session Git history. `.ekzd/project.toml` is separately required to stay committed and clean, its active contract is sourced from the committed `HEAD` blob, and it is protected from active-session commits.

## Context, prompt, task, and status

`ekzd prompt` renders the implementation-facing semantic contract for an active V1.1 session. Legacy/API-started sessions that lack the portable V1.1 workflow metadata cannot produce this handoff; start a fresh CLI session with an explicit `--branch`. A legacy session whose recorded baseline was dirty is likewise rejected because another environment could not reproduce that state from the baseline commit alone.

The prompt records the sanitized repository identifier captured at session start when available, project, baseline branch and HEAD, declared implementation branch, clean baseline requirement, objective, declared sources, allowed and excluded scope patterns, constraints, authority, acceptance criteria, configured verification steps, and maximum commit count. Verification commands are rendered as JSON argv arrays plus their working directories so argument boundaries remain lossless rather than being flattened into shell-looking strings.

The generated instructions are environment-agnostic: a development environment with a local checkout should validate its worktree, while an environment with repository/API access should not pretend that local Git checks were performed. The handoff asks capable environments to work from the exact baseline on the declared task branch, run/fix/retest checks, self-review, push/update the task branch, and open/update the PR. It explicitly prohibits merging and requires unsupported PR operations to be reported as limitations.

`ekzd task` renders only the current task schema-v2 authority that worker checking can enforce. It uses the same frozen objective, session-start HEAD, implementation branch, sanitized repository metadata, and committed session contract as the semantic prompt, plus the exact executing EkzD version/build identity. It excludes mutable current HEAD/worktree data, timestamps, absolute paths, output destinations, project verification steps, declared sources, prose constraints, and authority prose. For an unchanged session and EkzD build, the bytes are stable regardless of where inside the project the command is invoked or which safe external output destination is selected. A dirty legacy baseline, invalid session contract, protected-session-history violation, or unavailable build identity blocks export rather than producing portable authority.

`ekzd status` renders the workflow-facing state. It is intended to answer the practical question "what am I supposed to do next?" without making the operator reconstruct the procedure from documentation. It distinguishes the frozen baseline branch and HEAD, declared implementation branch, and current checkout. A newly started unchanged session directs the operator to both the semantic `ekzd prompt` handoff and the machine `ekzd task` manifest. It also reports successful verification as `stale` after exact-state drift, and reports known unrecoverable-in-session conditions such as exceeded commit budget or non-descendant history as `blocked` with restart/abort guidance.

`ekzd context` renders a human-friendly view of the current objective, session policy, Git state, scope, authority, acceptance criteria, and verification plan. `--json` provides the same committed project contract as structured data for tools that need it. When a session is active, EkzD refuses to render trusted contract outputs if the canonical `.ekzd/project.toml` is dirty, staged, uncommitted, historically touched during the session, or no longer matches the session-start committed digest. The project contract itself is parsed from `HEAD:.ekzd/project.toml`, keeping prompt generation, task export, verification, and acceptance bound to the same committed definition. Portable V1.1 workflow metadata such as the declared task branch and frozen repository identifier remains local in `.ekzd/session.json`.

The `sources.paths` entries identify regular files to consult at the frozen baseline. Active operations validate them against that commit, so an authorized implementation may delete or rename them; they are not preservation rules. EkzD exposes those paths as part of the semantic prompt contract; V1 does not semantically read or enforce their prose by itself, and task export does not pretend they are worker-enforced manifest authority.

Likewise, free-text authority and constraint entries are contextual instructions for the AI or human operator. EkzD mechanically enforces what it can measure: canonical configuration integrity, protected harness-history integrity, local session-state integrity, supported Git-visible scope, commit budget, verification results, exact-state binding, and explicit acceptance. Those prose entries remain in `ekzd prompt`; they are intentionally absent from the worker task manifest because the current task schema does not mechanically enforce them.

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
python3 -m unittest discover -s tests -v
python3 -m compileall -q src tests
```

The repository history before EkzD is retained as the historical WhiteTree Workflow Manager experiment. The current implementation replaces that mechanism rather than extending it.
