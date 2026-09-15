# EkzD Verification and Acceptance Standard

This document defines the V1 acceptance contract and the V1.1 workflow that carries work into that contract. It is intentionally stricter than simply running tests once.

## Verification

A verification result is valid only for the exact supported Git-visible state that produced it and only under the canonical committed harness configuration captured when the session started.

EkzD records:

- the current Git commit (`HEAD`);
- the current branch;
- the complete porcelain worktree status;
- the supported Git-visible state fingerprint;
- the committed project configuration digest;
- the changed paths considered for scope enforcement;
- the active session's final commit count and configured maximum;
- each verification command argv array, working directory, exit code, and bounded output.

Verification fails closed when configuration is invalid, uncommitted, staged, unstaged, changed after session start, or historically touched during the active session; when the session commit budget is exceeded; when scope is violated; when tracked paths use Git index flags that suppress normal change visibility; when any tracked path has an active Git `filter` attribute; when local session state is tracked or changes during verification; when a working directory escapes the repository; when a command times out; when a configured command cannot be launched; or when any configured command fails.

Before commands run, EkzD requires the canonical `.ekzd/project.toml` to remain a regular committed clean path, loads and validates the contract from the raw `HEAD:.ekzd/project.toml` blob, rejects unsupported Git visibility features, validates the active session contract, protected harness history, commit budget, and changed-path scope, and pins the active local session state for the verification attempt. After the configured commands finish, EkzD reloads the contract from the committed `HEAD` blob, again requires the canonical project path to remain committed and clean, requires the local session state to remain untracked and unchanged, and rechecks the frozen configuration digest, protected harness history, commit budget, supported Git visibility, and scope against the original pinned session state before recording verification evidence. This prevents a verifier from redefining the session baseline, staging a different harness contract, silently changing contextual instructions, publishing local session metadata through accepted history, exceeding the session budget, hiding tracked changes with unsupported Git visibility features, or creating Git-visible changes outside the declared scope while still producing a green verification result.

Changed-path discovery is rename-safe and NUL-delimited. EkzD disables Git rename collapsing for scope evaluation so both sides of a move are evaluated independently, and it parses Git paths without relying on newline-delimited or quoted output. For committed work, scope is historical rather than net-only: EkzD unions the paths touched by every commit reachable in the active session range from the recorded starting `HEAD` through the current `HEAD`. A path therefore remains part of scope evaluation even when a later session commit restores its contents to the starting version.

## Canonical project contract

`.ekzd/project.toml` is V1's canonical project contract. Before `ekzd start`, it must already exist in `HEAD` as a regular tracked file and must have no staged or unstaged changes. V1 intentionally does not support starting a session from an untracked, staged-only, locally dirty, or symlinked project contract. The V1.1 CLI additionally requires the repository working tree to be clean before starting so the frozen baseline can be reproduced by a separate implementation environment.

For active-session semantics, the committed Git blob is authoritative. EkzD parses and hashes the raw `HEAD:.ekzd/project.toml` bytes rather than the worktree copy. Ordinary checkout transformations such as line-ending conversion cannot redefine the scope, authority, sources, session policy, acceptance criteria, or verification commands EkzD enforces.

V1 rejects any tracked path with an active Git `filter` attribute. Clean/smudge filters can make Git's diff representation differ from the physical worktree bytes executed by verification commands, so EkzD does not attempt to reason through them in V1. This also means Git LFS repositories or paths using custom content filters are unsupported during an EkzD session.

This gives the single-operator workflow one reproducible definition of the harness contract inside a conventional supported checkout. GitHub, CI, `ekzd context --json`, local verification, and final acceptance all anchor to the same committed blob, while the worktree and index path are still required to remain clean so active-session edits cannot be silently introduced.

When `ekzd start` creates a session, it records the digest of the raw committed `.ekzd/project.toml` blob. While that session is active, `ekzd context`, `ekzd prompt`, `ekzd verify`, and `ekzd finish --accept` require the path to remain clean and the committed blob digest to match the recorded digest. If configuration must change, abort or finish the session first, edit and commit the configuration outside an active session, then start a fresh session.

## V1.1 portable workflow metadata

The V1.1 CLI requires an explicit implementation/task branch through `ekzd start "..." --branch <task-branch>`. This branch is stored separately from the baseline branch and baseline `HEAD`. A non-detached baseline branch cannot also be declared as the implementation branch, preventing a session started on `main` from producing a handoff that instructs direct implementation on `main`. A detached baseline remains representable, but the implementation branch must still be a real declared Git branch name.

At session start EkzD reads `remote.origin.url` when available, sanitizes it, and stores the resulting repository identifier in local session metadata. Userinfo, credentials, query parameters, and fragments are excluded from the rendered identifier. If no origin exists, the session records that no remote locator was available rather than inventing one. Because the value is captured at session start, changing or removing `origin` later does not change `ekzd prompt`.

This workflow metadata remains in `.ekzd/session.json`; it is not added to `.ekzd/project.toml` and does not make GitHub part of the trust boundary. EkzD still performs no GitHub authentication or network orchestration.

## Runtime build identity and worker task schemas

EkzD 0.2.0 has an exact runtime build identity for stateless worker checking. The semantic version is defined once in `ekzd.__version__`, and package metadata derives its version from that source. `ekzd --version` reports that semantic version plus the exact 64-character lowercase SHA-256 for the Python source files of the executing `ekzd` package and does not require an EkzD project checkout.

The runtime build SHA-256 is independent of Git history, Git metadata, the absolute installation directory, bytecode, cache files, wheel `RECORD`, `dist-info`, and other installer-specific metadata. EkzD recursively enumerates the executing package's `.py` files, orders them by POSIX package-relative path, normalizes CRLF and lone CR source line endings to LF, and binds both each relative path and the corresponding normalized bytes into the digest with length-delimited separators. A symlinked or unreadable Python source path, an unusable source path, or failure to determine the package source set reliably makes exact build identity unavailable. In that condition `ekzd --version` exits non-zero rather than substituting `unknown` or another placeholder.

Standalone worker task manifests support schema versions 1 and 2. Schema v1 remains compatible with its existing baseline, repository, authority, scope, commit-ceiling, and verification semantics. Because schema v1 has no harness-build pin, worker checking adds a non-blocking `WARN` explaining that the task does not pin the EkzD harness build; otherwise its worker behavior remains unchanged.

Schema v2 adds a required `[ekzd]` table with a non-empty `version` and an exact 64-character hexadecimal `build_sha256`. Before evaluating Git visibility, baseline ancestry, branch, scope, repository identity, commit ceilings, project verification, or task verification, worker checking determines the executing EkzD identity and compares both required fields. A version or build mismatch is a blocking failure and returns before any project or task verification command executes. If exact executing build identity cannot be determined, checking is blocking `UNAVAILABLE` and cannot report readiness.

Every worker result carries the executing EkzD semantic version and build SHA-256 in structured output when the identity is available, and human-readable `ekzd check` output shows the same values. This harness-build identity gate is additive to the existing worker trust model; it does not change `.ekzd/project.toml` schema or weaken baseline ancestry, implementation-branch, worktree-mode, repository-identity, scope, commit-ceiling, protected-history, Git-visibility, verifier-mutation, aggregation, or final clean-worktree checks.

## Protected harness history

V1 protects exactly two trust-critical paths from active-session commit history:

- `.ekzd/project.toml`;
- `.ekzd/session.json`.

This protection is independent of `scope.include` and `scope.exclude`. A repo-wide project scope such as `include = ["*"]` does not authorize committing either path while a session is active. If either path appears in any commit reachable in the active session range, that session is invalid even when a later commit restores or untracks the file.

The protected set is intentionally limited to these two files rather than the entire `.ekzd/` directory. Future non-trust-critical EkzD artifacts may therefore evolve without inheriting a blanket history prohibition. Adding another trust-critical harness file in a future version requires explicitly adding it to the protected set.

The historical rule is stricter than the current-byte digest alone: committing `.ekzd/project.toml` during the session permanently invalidates that session even if a later commit restores the exact original bytes.

## Session-state integrity

`.ekzd/session.json` is trusted operator-local harness metadata. It must remain untracked by Git and must never appear in active-session commit history. EkzD refuses to read or write session state while the path is currently tracked, and protected-history enforcement also rejects a session where the file was committed and later removed from tracking.

V1.1 stores the explicit implementation branch and frozen sanitized repository identity in this local session state alongside the objective, baseline Git state, session policy, verification evidence, and acceptance state. A portable implementation handoff therefore does not need to infer those values from later mutable Git configuration.

At the start of verification, EkzD keeps the parsed session contract in memory and records the local state file digest. Verification commands may run ordinary project code, so after they finish EkzD requires the on-disk session state to still match the state that existed before the commands ran. Post-verification budget and scope checks use the original pinned session contract rather than trusting a replacement baseline written by the verifier.

This is integrity checking for the normal V1 workflow, not an operating-system sandbox. V1 does not claim to defend against a hostile same-user process with arbitrary control over the EkzD process, filesystem, or Git internals.

## Session budget

Each session has a positive `session.max_commits` limit. The V1 default is 3 commits, and projects may configure a different positive limit before starting a session.

The selected limit is captured when the session starts. Changing the configured budget during an active session does not retroactively enlarge that session; the operator must begin a fresh session to use a different budget.

Verification counts commits reachable from the session's starting `HEAD` to the current `HEAD`. If the current history no longer descends from the recorded starting commit, verification fails instead of guessing how much work belongs to the session.

The commit budget is a bounded-work policy, not a claim that commit count directly measures code quality. Its purpose is to keep one objective reviewable and to create a natural handoff point before unrelated work accumulates.

If a session exceeds its budget or otherwise cannot be completed under its original contract, `ekzd abort` closes it without acceptance. Aborting changes only local EkzD session state; it does not revert project files, commits, or Git history.

## Workflow status guidance

`ekzd status` is advisory workflow guidance layered on top of the same fail-closed trust checks. For an active V1.1 session it distinguishes the baseline branch and `HEAD`, declared implementation branch, and current branch rather than collapsing them into one ambiguous branch value.

The workflow-facing verification state is reported as:

- `not run` when no relevant verification attempt has been recorded;
- `passed` only when verification succeeded and the current exact supported Git-visible state still matches the verified state;
- `stale` when verification previously passed but the Git state or exact-state fingerprint changed afterward;
- `failed` when the most recent verification attempt failed; and
- `blocked` for explicitly handled session-invalid conditions where recommending another normal verification would be misleading.

An exceeded commit budget and history that no longer descends from the frozen baseline are reported as `blocked` with guidance to abort and restore/restart from a contract-valid baseline. This does not weaken verification: `ekzd verify` and `ekzd finish --accept` continue to enforce their existing fail-closed rules independently of status output.

## Scope

`scope.include` is a required allowlist for a ready session. At least one include pattern must be configured before work can start. A deliberately repo-wide scope must still be explicit, for example `include = ["*"]`.

`scope.exclude` is always a denylist. A changed path matching an exclude pattern blocks verification even when it also matches an include pattern.

Directory patterns ending in `/` match that directory and its descendants. Other patterns use case-sensitive glob matching.

Committed scope includes every path touched by every commit in the active session history, not only files whose final contents differ from the starting tree. Reverting an out-of-scope change in a later commit does not erase that path from the session's scope evidence. Current staged, unstaged, and untracked changes are then unioned with that committed history before the allowlist and denylist are evaluated.

Protected harness history and canonical-config cleanliness are evaluated separately from project scope. `.ekzd/project.toml` cannot be dirty, staged, uncommitted, symlinked, or present in active-session commits regardless of project scope. Its active semantics are sourced only from the committed `HEAD` blob. `.ekzd/session.json` cannot be tracked or appear in active-session commits. Only the current untracked `.ekzd/session.json` file is omitted from ordinary project changed-path evaluation because it is required local harness metadata.

EkzD rejects tracked paths marked with Git's `assume-unchanged` or `skip-worktree` index flags because those flags intentionally suppress ordinary status/diff visibility and would weaken deterministic scope and exact-state checks. V1 therefore does not support operating inside a sparse-checkout state that depends on `skip-worktree`; clear those flags or use a normal checkout before starting or verifying an EkzD session.

EkzD also rejects every tracked path whose resolved Git `filter` attribute is active. Content filters can normalize or transform a changed worktree file before Git computes a diff, allowing physical bytes to differ while ordinary diff output remains empty. V1 therefore supports only conventional unfiltered tracked files for scope and exact-state guarantees; Git LFS and custom clean/smudge filters are outside the supported checkout model.

## Context integrity

`ekzd context` and `ekzd context --json` are part of the active committed project contract, not merely convenience renderers. When a session is active, EkzD refuses to emit context unless the canonical path is still clean, the committed `HEAD` blob still matches the session-start digest, `.ekzd/project.toml` has not appeared in active-session commit history, and the repository remains within V1's supported Git visibility model. The context itself is parsed from that committed blob rather than the worktree copy.

This matters because sources, authority, and free-text constraints are intentionally contextual rather than semantically enforced by V1. `ekzd prompt` combines that same committed frozen contract with the V1.1 portable workflow metadata captured in local session state, so the implementation handoff does not require a separate prompt-generator session to reinterpret the contract.

`ekzd context --json` remains available as the lower-level structured interface for tools that need machine-readable project-contract data.

## AI-assisted operating model

EkzD does not need to be the code generator to enforce this standard.

In the recommended V1.1 workflow, the user and maintainer/planning session define the objective and contract. The operator starts a clean local EkzD session with an explicit task branch. `ekzd prompt` then renders the normal deterministic implementation handoff. A separate implementation session consumes that handoff, operates from the exact frozen baseline on the declared task branch, implements the scoped change, runs available checks, fixes failures, self-reviews, and pushes/updates the task branch and pull request when its environment supports those repository operations.

The implementation handoff explicitly prohibits merging. If the implementation environment cannot push or create/update a PR, it must report that limitation instead of claiming the operation happened. Authoritative `ekzd verify` remains in the maintainer environment that owns `.ekzd/session.json`, and final merge/reject authority remains with the operator after review.

This means V1.1 still provides deterministic post-work enforcement rather than real-time control over a remote AI session. A remote code generator can technically create an invalid commit; EkzD's responsibility is to refuse verification or acceptance when that Git-visible result violates the measurable contract. EkzD does not authenticate to GitHub, open PRs itself, route agents, or autonomously merge work.

Free-text authority, source, and constraint entries remain instructions for the AI or human operator. The V1 verifier mechanically enforces canonical configuration integrity, protected harness-history integrity, local session-state integrity, supported Git visibility, historical and current path scope, session commit budget, configured commands, exact-state binding, and explicit acceptance.

## Acceptance

Acceptance is a separate stage from verification.

`ekzd finish --accept` must reject the task unless all of the following are true:

1. an active local, untracked session exists;
2. `.ekzd/project.toml` remains a regular committed clean path and the raw committed blob matches the digest captured at session start;
3. neither `.ekzd/project.toml` nor `.ekzd/session.json` appears in active-session commit history;
4. configured verification completed successfully;
5. the active session still satisfies its original commit budget;
6. tracked paths do not use `assume-unchanged`, `skip-worktree`, or an active Git `filter` attribute to weaken Git visibility;
7. the verified committed project configuration digest is unchanged;
8. Git HEAD is unchanged from the verified state;
9. the branch is unchanged from the verified state;
10. the worktree status is unchanged from the verified state;
11. the supported Git-visible state fingerprint is unchanged from the verified state;
12. historical and current scope still pass; and
13. the operator explicitly supplies `--accept` after the required review.

`--accept` is an operator attestation. V1 does not authenticate the operator's identity or cryptographically prove that a particular human performed the review.

Acceptance requires the current tracked, staged, unstaged, and untracked state to exactly match the state bound to successful verification within V1's supported conventional checkout model. If the current state differs, the prior verification cannot be accepted and verification must be rerun. The `stale` state shown by `ekzd status` mirrors this rule for operator guidance; it does not replace the acceptance checks themselves.

Git-ignored files are outside V1's exact-state fingerprint. A project that needs an ignored file to affect acceptance must check that requirement through a configured verification command. V1 does not recursively hash every ignored file by default. Tracked files with active content filters are rejected rather than accepted under a transformed Git representation.

## What this standard does not prove

Passing verification does not prove that requirements were interpreted correctly, that tests are complete, that free-text project guidance was followed semantically, or that a change is desirable. Acceptance criteria may include product and code-quality judgments that require human review.

EkzD therefore records explicit operator acceptance rather than treating a successful command as automatic authorization to merge or deploy.

## V1 portability rule

Project configuration must use repository-relative paths and command arrays. EkzD runs commands directly without a shell. This keeps command behavior deterministic and avoids shell interpolation becoming part of the harness contract. `ekzd prompt` renders configured verification commands as JSON argv arrays plus their working directories so the implementation handoff preserves argument boundaries rather than presenting an ambiguous shell-like string.

V1 assumes a conventional checkout: no active tracked-file content filters or Git LFS, no `assume-unchanged`, and no `skip-worktree`/sparse-checkout state. Future versions may add stronger support for transformed or virtualized working trees, but V1 fails closed instead of guessing how such features affect scope or exact-state evidence.

Future schema versions may add stronger evidence or policy gates, but V1 should remain backward-compatible within `.ekzd/project.toml` `schema_version = 1`. Stateless worker task manifests separately support task schema versions 1 and 2; adding task schema v2 does not change the project-contract schema.
