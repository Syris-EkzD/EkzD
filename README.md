# EkzD

EkzD is a small, project-agnostic development harness for scoped and verifiable AI-assisted work.

It does not run an AI model, orchestrate agents, replace Git, or replace CI. Its job is narrower: define the working contract for a task, expose that contract to the tools or people doing the work, keep each session bounded, run deterministic local verification, and refuse acceptance unless the verified state still matches the state being accepted.

EkzD is deliberately not the code generator. In the intended V1.1 workflow, an AI coding session, Codex, or a human produces the changes; EkzD provides the frozen contract, deterministic implementation handoff, workflow guidance, and acceptance gate around that work. The harness remains optimized for a single-operator personal workflow rather than a multi-user policy platform.

## Recommended V1.1 workflow

V1.1 keeps planning/maintenance and implementation as separate responsibilities without requiring the operator to manually translate EkzD JSON into an implementation prompt.

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
        ↓
Implementation session
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
- **EkzD** freezes the local session contract and portable workflow metadata, renders the deterministic implementation handoff, shows the current workflow state, and checks the Git-visible result against that contract.
- **Implementation session** consumes the handoff, works on the declared task branch, produces and tests the actual repository changes, and opens or updates a PR when its environment supports those operations.
- **GitHub** carries branches, commits, pull requests, and collaboration history.
- **CI** independently executes remote checks where appropriate.

The active session state lives in `.ekzd/session.json`, which is intentionally local and gitignored. EkzD requires that path to remain untracked and also rejects an active session if `.ekzd/session.json` appears in any commit made during that session, even when a later commit removes it from tracking again. An implementation session operating only through GitHub therefore cannot legitimately replace or publish that local contract through accepted repository history.

`ekzd start` requires an explicit implementation/task branch. The session records the baseline branch and HEAD separately from that declared implementation branch, so starting from `main` does not authorize implementation directly on `main`. A detached baseline is represented as detached, but it still requires a real declared task branch for the portable handoff.

At session start EkzD also captures a sanitized repository identifier derived from `remote.origin.url` when one exists. Userinfo, credentials, query parameters, and fragments are excluded. That identifier is frozen in local session metadata, so later changes or removal of `origin` do not silently change the implementation handoff. If no origin exists at session start, EkzD records that fact instead of inventing a remote locator.

`ekzd prompt` is the V1.1 bridge between the local trusted session and a separate implementation environment. It renders the frozen objective, repository baseline, baseline branch, implementation branch, repository identity metadata, sources, scope, authority, acceptance criteria, verification plan, and session budget into an environment-agnostic implementation handoff. It explicitly distinguishes environments with a local Git checkout from environments that only have repository/API access. It also instructs the implementation session to use logical commits, run/fix/retest available checks, self-review, push/update the declared branch and PR when supported, never merge the PR, and report limitations when repository operations are unavailable.

`ekzd context --json` remains available as the lower-level structured contract for tools that need machine-readable data. EkzD still does not authenticate to GitHub, create PRs itself, or control an AI's GitHub actions in real time. Instead, it validates the resulting Git state after that work is returned to the environment where the trusted EkzD session is running.

## Commands

```sh
ekzd
ekzd init
ekzd start "implement registration validation" --branch feat/registration-validation
ekzd status
ekzd prompt
ekzd context
ekzd context --json
ekzd verify
ekzd handoff --done "implemented validation" --next "review edge cases"
ekzd finish --accept
```

`ekzd status` is the operator-facing workflow compass. For an active session it shows the objective, baseline branch and HEAD, declared implementation branch, current branch, worktree state, commit budget, verification state, and the next expected action. Its verification state is one of the workflow-facing states `not run`, `passed`, `stale`, `failed`, or `blocked` as appropriate. A previously successful verification becomes `stale` if the exact supported Git-visible state changes. An exceeded commit budget or history that no longer descends from the frozen baseline is shown as `blocked` instead of suggesting verification can continue normally.


## Stateless worker checking

`ekzd check` is a separate worker-side path for implementation environments that should validate a frozen task without owning EkzD's local session lifecycle. Unlike `start`, `verify`, and `finish`, worker checking does not require, read, or update `.ekzd/session.json`. The existing session workflow remains the maintainer/operator acceptance path.

Worker authority comes from a standalone schema-version-1 TOML task manifest. The file may live outside the repository, and every result identifies the exact manifest bytes with a SHA-256 digest. Repository-level verification is still trusted from `.ekzd/project.toml` at the manifest's frozen baseline commit; optional task verification steps are additive and cannot replace those project checks.

A minimal task manifest looks like this:

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

During implementation, run a development check against the current HEAD plus staged, unstaged, and untracked non-ignored work:

```sh
ekzd check --task /path/to/task.toml
ekzd check --task /path/to/task.toml --json
```

Before handoff, run final checking:

```sh
ekzd check --final --task /path/to/task.toml
```

Final checking requires a clean worktree and binds a successful result to the exact final HEAD. Both modes verify baseline ancestry, the declared implementation branch, the commit ceiling, protected EkzD paths and supported Git visibility, task scope, optional repository identity, and all baseline project plus additive task verification commands. Verification commands are executed directly as argv arrays, never through a shell. EkzD does not repair, format, stage, commit, reset, restore, or otherwise rewrite the candidate; if a configured verifier changes Git-visible project state, the check fails and leaves that mutation visible for inspection.

Each individual result uses one of four states: `PASS` means the check ran and succeeded; `FAIL` means it ran and found a blocking violation; `UNAVAILABLE` means a required check could not be evaluated or executed and is blocking; `WARN` is a non-blocking observation. Overall readiness succeeds only when no required result is `FAIL` or `UNAVAILABLE`. Ordinary independent failures are aggregated where safe so one failed command does not hide unrelated results.

If an active session cannot or should not be accepted, close it without acceptance:

```sh
ekzd abort
```

`abort` changes only local EkzD session state. It does not revert project files, commits, or Git history, and it never records acceptance.

`ekzd init` creates `.ekzd/project.toml` and ignores `.ekzd/session.json`. Before `ekzd start`, configure and commit `.ekzd/project.toml`. V1.1 also requires the working tree to be clean when starting through the CLI so the recorded task baseline can be reproduced by a separate development environment. Session state remains local and disposable.

## Terminal interface

Running bare `ekzd` in a supported interactive terminal launches EkzD's persistent terminal session. The session uses an alternate screen when the terminal safely supports it and presents active-task and idle states differently. An active task keeps the existing framed operational dashboard with the current task, branch, session, verification, commit budget, and recommended next action. When no task is active, EkzD instead shows a concise landing card that identifies the project, explains that EkzD is ready for a new task, summarizes its purpose, and points to the next action without inventing branch, verification, or commit placeholders.

Selecting **Start task** in the bare interactive experience collects the proposed objective and implementation branch, then pauses before session creation for an explicit review of the currently committed `.ekzd/project.toml` contract. The review summarizes the project, source and scope counts, configured verification steps, and session commit limit. The operator must choose to confirm and start, update the contract first, or cancel. Updating first or canceling creates no session; the update path directs the operator to edit and commit `.ekzd/project.toml` before retrying. EkzD does not try to infer whether the contract semantically matches the objective. This review gate applies only to the interactive Start task flow; explicit `ekzd start` keeps its existing command contract.

If the latest session was finished or aborted, the idle screen may show a bordered secondary **Previous task** card using only the latest persisted session state. It clearly labels the historical outcome and objective and, when available, the implementation branch, verification state, and result/acceptance state. Finished/accepted and passed values use success semantics, aborted/stale/not-run values use warning semantics, and failed/blocked/error values use failure semantics. With no previous session, the card is omitted. Persistent idle actions remain focused on starting a task or exiting, while the fallback/non-persistent interaction may still expose `View task`. Explicit `ekzd status` remains unchanged.

Persistent mode prioritizes the authoritative current dashboard over transcript history. Normal interactive actions replace the small recent-feedback area with a concise success, warning, or failure message instead of stacking full command summaries across redraws; the fallback scrolling interface may continue printing sequential command output. The terminal session remains a presentation/controller over the existing EkzD operations and does not duplicate verification, acceptance, scope, Git, or trust logic.

Selecting **Generate implementation prompt** opens the complete deterministic implementation contract inside the same persistent EkzD alternate-screen session in an `EkzD · Implementation Prompt` viewer. The viewer supports lightweight keyboard navigation with Up/Down, Page Up/Page Down, Home/End, `c` or `C` to copy the complete exact contract to the system clipboard, and Enter, Escape, or `q` to return. Copying happens only after that explicit action; opening the viewer does not alter the clipboard. If no supported clipboard mechanism succeeds, the viewer reports clipboard unavailability and remains usable. Returning redraws the normal persistent EkzD home screen. The viewer uses the same exact generated contract as the existing prompt operation; it does not maintain a separate formatter. The explicit `ekzd prompt` command remains deterministic raw plain text for piping, automation, or direct handoff.

EkzD degrades conservatively when terminal screen control is unsuitable. Non-TTY bare invocation never waits for input, `TERM=dumb` and very small or unsupported terminals use the plain scrolling interactive presentation, and alternate-screen cleanup restores the previous terminal display on normal exit and failure paths. In supported persistent terminals, EkzD also suppresses alternate-screen mouse-wheel translation so wheel activity does not become menu input, then restores the terminal's prior scroll mode and display state when the session exits.

Human-facing color remains semantic:

- green: successful, passed, finished, accepted, or clean state;
- red: failure, blocked, or error state;
- yellow: warning, aborted, stale, or not-run state;
- cyan/bold: structural headings and important interactive labels;
- dim text: genuinely secondary explanatory details and low-priority metadata.

Colors are enabled automatically only for interactive terminals. They are disabled for redirected/piped output, when `TERM=dumb`, or when the standard `NO_COLOR` environment variable is present. Screen-control sequences used by a supported persistent terminal are independent of color styling. `ekzd context --json` always remains machine-readable JSON without ANSI styling, and `ekzd prompt` remains deterministic plain text suitable for direct handoff.

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

Configuration is intentionally explicit. Empty `scope.include`, empty acceptance criteria, or an empty verification plan prevent a session from starting. A deliberately repo-wide scope must still be explicit, for example `include = ["*"]`.

`.ekzd/project.toml` is the canonical committed project contract. `ekzd start` refuses to begin a CLI session unless that file already exists in `HEAD` as a regular tracked file, has no staged or unstaged changes, and the repository working tree is otherwise clean. The clean-start rule gives a separate implementation environment a reproducible commit baseline instead of depending on local-only edits that cannot be represented by the recorded HEAD.

The `--branch` value supplied to `ekzd start` is portable workflow metadata, not a replacement for the canonical project contract. EkzD validates it as a Git branch name, refuses to reuse the non-detached baseline branch as the implementation branch, and stores it in the local active session along with the frozen sanitized repository identity.

For an active session, EkzD parses and hashes the raw `HEAD:.ekzd/project.toml` blob rather than the worktree copy, so ordinary checkout transformations such as line-ending conversion cannot redefine the contract. `ekzd prompt`, `ekzd context`, `ekzd verify`, and `ekzd finish --accept` all remain bound to that committed contract while continuing to require the worktree/index config path to remain clean.

V1 deliberately rejects any tracked path with an active Git `filter` attribute. Clean/smudge filters can make Git's diff representation differ from the actual worktree bytes that verification commands execute, weakening scope and exact-state guarantees. This means Git LFS and repositories that rely on custom tracked-file filters are not supported by EkzD V1. Remove those filter attributes or use a conventional checkout before starting or verifying a session.

Changing scope, authority, sources, session policy, acceptance criteria, verification commands, or any other project harness configuration therefore requires ending the current session first. Abort or finish the session, edit and commit `.ekzd/project.toml`, then start a fresh session. Any active-session commit that touches `.ekzd/project.toml` permanently invalidates that session even if a later commit restores the exact original bytes.

The two V1 protected harness-history paths are exactly `.ekzd/project.toml` and `.ekzd/session.json`. Their protection is independent of `scope.include` and `scope.exclude`; even `include = ["*"]` cannot authorize committing either path during an active session. This is intentionally narrower than protecting the entire `.ekzd/` directory so future non-trust-critical EkzD artifacts can evolve without inheriting an unnecessary blanket restriction.

`session.max_commits` is a hard session-size safety ceiling. `ekzd init` writes `20`, and a missing value also resolves to `20`. Projects may configure a smaller positive integer, but values above `20` are rejected rather than clamped. The configured value is captured when the session starts; it cannot be raised mid-session to excuse work that has already exceeded its original boundary.

The ceiling is not a target. Implementation work should prefer small, logically isolated commits when multiple commits improve reviewability, without creating meaningless micro-commits. Unrelated changes should not be combined merely to reduce commit count. If a task approaches its configured ceiling, stop and reassess or report that the task may be exceeding its intended scope rather than creating oversized commits to stay under the limit.

## Acceptance model

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

- verification passed;
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

The exact-state fingerprint covers tracked, staged, unstaged, and untracked state under V1's supported conventional Git checkout model. Git-ignored files are outside V1's fingerprint unless a configured verification command explicitly checks them. Tracked files using content filters are rejected rather than fingerprinted through a potentially transformed Git view. `.ekzd/session.json` is handled separately as trusted local harness metadata: it must stay untracked, must not change while verification commands run, and must never enter active-session Git history. `.ekzd/project.toml` is separately required to stay committed and clean, its active contract is sourced from the committed `HEAD` blob, and it is protected from active-session commits.

## Context, prompt, status, and handoff

`ekzd prompt` renders the implementation-facing contract for an active V1.1 session. Legacy/API-started sessions that lack the portable V1.1 workflow metadata cannot produce this handoff; start a fresh CLI session with an explicit `--branch`. A legacy session whose recorded baseline was dirty is likewise rejected because another environment could not reproduce that state from the baseline commit alone.

The prompt records the sanitized repository identifier captured at session start when available, project, baseline branch and HEAD, declared implementation branch, clean baseline requirement, objective, declared sources, allowed and excluded scope patterns, constraints, authority, acceptance criteria, configured verification steps, and maximum commit count. Verification commands are rendered as JSON argv arrays plus their working directories so argument boundaries remain lossless rather than being flattened into shell-looking strings.

The generated instructions are environment-agnostic: a development environment with a local checkout should validate its worktree, while an environment with repository/API access should not pretend that local Git checks were performed. The handoff asks capable environments to work from the exact baseline on the declared task branch, run/fix/retest checks, self-review, push/update the task branch, and open/update the PR. It explicitly prohibits merging and requires unsupported PR operations to be reported as limitations.

`ekzd status` renders the workflow-facing state. It is intended to answer the practical question "what am I supposed to do next?" without making the operator reconstruct the procedure from documentation. It distinguishes the frozen baseline branch and HEAD, declared implementation branch, and current checkout. It also reports successful verification as `stale` after exact-state drift, and reports known unrecoverable-in-session conditions such as exceeded commit budget or non-descendant history as `blocked` with restart/abort guidance.

`ekzd context` renders a human-friendly view of the current objective, session policy, Git state, scope, authority, acceptance criteria, and verification plan. `--json` provides the same committed project contract as structured data for tools that need it. When a session is active, EkzD refuses to render trusted contract outputs if the canonical `.ekzd/project.toml` is dirty, staged, uncommitted, historically touched during the session, or no longer matches the session-start committed digest. The project contract itself is parsed from `HEAD:.ekzd/project.toml`, keeping prompt generation, verification, and acceptance bound to the same committed definition. Portable V1.1 workflow metadata such as the declared task branch and frozen repository identifier remains local in `.ekzd/session.json`.

The `sources.paths` entries define the project material an AI or developer is expected to consult. EkzD exposes those paths as part of the contract; V1 does not semantically read or enforce their prose by itself.

Likewise, free-text authority and constraint entries are contextual instructions for the AI or human operator. EkzD mechanically enforces what it can measure: canonical configuration integrity, protected harness-history integrity, local session-state integrity, supported Git-visible scope, commit budget, verification results, exact-state binding, and explicit acceptance.

`ekzd handoff` records semantic progress (`--done`) and next actions (`--next`) in the local session state. It does not modify project documentation automatically.

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
```

Run its tests with:

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q src tests
```

The repository history before EkzD is retained as the historical WhiteTree Workflow Manager experiment. The current implementation replaces that mechanism rather than extending it.
