# EkzD Verification and Acceptance Standard

This document defines the V1 acceptance contract. It is intentionally stricter than simply running tests once.

## Verification

A verification result is valid only for the exact Git-visible state that produced it and only under the harness configuration captured when the session started.

EkzD records:

- the current Git commit (`HEAD`);
- the current branch;
- the complete porcelain worktree status;
- the Git-visible state fingerprint;
- the project configuration digest;
- the changed paths considered for scope enforcement;
- the active session's final commit count and configured maximum;
- each verification command, working directory, exit code, and bounded output.

Verification fails closed when configuration is invalid or changed after session start, the session commit budget is exceeded, scope is violated, tracked paths use Git index flags that suppress normal change visibility, local session state is tracked or changes during verification, a working directory escapes the repository, a command times out, a configured command cannot be launched, or any configured command fails.

Before commands run, EkzD validates the ready project configuration, active session contract, commit budget, Git index visibility, and changed-path scope, and pins the active local session state for the verification attempt. After the configured commands finish, EkzD reloads the ready configuration, requires the local session state to remain untracked and unchanged, and rechecks the frozen configuration digest, commit budget, Git index visibility, and scope against the original pinned session state before recording verification evidence. This prevents a verifier from redefining the session baseline, silently changing the contract, exceeding the session budget, hiding tracked changes with Git index flags, or creating Git-visible changes outside the declared scope while still producing a green verification result.

Changed-path discovery is rename-safe and NUL-delimited. EkzD disables Git rename collapsing for scope evaluation so both sides of a move are evaluated independently, and it parses Git paths without relying on newline-delimited or quoted output. For committed work, scope is historical rather than net-only: EkzD unions the paths touched by every commit reachable in the active session range from the recorded starting `HEAD` through the current `HEAD`. A path therefore remains part of scope evaluation even when a later session commit restores its contents to the starting version.

## Immutable session contract

When `ekzd start` creates a session, it records the digest of the complete `.ekzd/project.toml` file.

Verification and acceptance require the current configuration to match that starting digest. Scope, authority, session policy, acceptance criteria, verification commands, and other harness settings therefore cannot be loosened or otherwise changed during an active session. Change the configuration only after aborting or finishing the current session, then start a fresh session.

## Session-state integrity

`.ekzd/session.json` is trusted operator-local harness metadata. It must remain untracked by Git. EkzD refuses to read or write session state when that path is tracked, so a pulled branch cannot legitimately replace the local session contract through repository history.

At the start of verification, EkzD keeps the parsed session contract in memory and records the local state file digest. Verification commands may run ordinary project code, so after they finish EkzD requires the on-disk session state to still match the state that existed before the commands ran. Post-verification budget and scope checks use the original pinned session contract rather than trusting a replacement baseline written by the verifier.

This is integrity checking for the normal V1 workflow, not an operating-system sandbox. V1 does not claim to defend against a hostile same-user process with arbitrary control over the EkzD process, filesystem, or Git internals.

## Session budget

Each session has a positive `session.max_commits` limit. The V1 default is 3 commits, and projects may configure a different positive limit before starting a session.

The selected limit is captured when the session starts. Changing the configured budget during an active session does not retroactively enlarge that session; the operator must begin a fresh session to use a different budget.

Verification counts commits reachable from the session's starting `HEAD` to the current `HEAD`. If the current history no longer descends from the recorded starting commit, verification fails instead of guessing how much work belongs to the session.

The commit budget is a bounded-work policy, not a claim that commit count directly measures code quality. Its purpose is to keep one objective reviewable and to create a natural handoff point before unrelated work accumulates.

If a session exceeds its budget or otherwise cannot be completed under its original contract, `ekzd abort` closes it without acceptance. Aborting changes only local EkzD session state; it does not revert project files, commits, or Git history.

## Scope

`scope.include` is a required allowlist for a ready session. At least one include pattern must be configured before work can start. A deliberately repo-wide scope must still be explicit, for example `include = ["*"]`.

`scope.exclude` is always a denylist. A changed path matching an exclude pattern blocks verification even when it also matches an include pattern.

Directory patterns ending in `/` match that directory and its descendants. Other patterns use case-sensitive glob matching.

Committed scope includes every path touched by every commit in the active session history, not only files whose final contents differ from the starting tree. Reverting an out-of-scope change in a later commit does not erase that path from the session's scope evidence. Current staged, unstaged, and untracked changes are then unioned with that committed history before the allowlist and denylist are evaluated.

EkzD rejects tracked paths marked with Git's `assume-unchanged` or `skip-worktree` index flags because those flags intentionally suppress ordinary status/diff visibility and would weaken deterministic scope and exact-state checks. V1 therefore does not support operating inside a sparse-checkout state that depends on `skip-worktree`; clear those flags or use a normal checkout before starting or verifying an EkzD session.

Untracked session state (`.ekzd/session.json`) is excluded from changed-path evaluation because it is harness metadata, not project output. If that path is tracked by Git, EkzD rejects the session-state operation instead of excluding the tracked file from the trust boundary.

## AI-assisted operating model

EkzD does not need to be the code generator to enforce this standard.

In the recommended V1 workflow, the local EkzD session defines the contract, `ekzd context --json` is handed to a prompt-generation/planning session, and a separate coding session implements the resulting prompt through GitHub. The resulting branch is then pulled into the environment where EkzD is running and verified against the original local contract.

This means V1 provides deterministic post-work enforcement rather than real-time control over a remote AI session. A remote code generator can technically create an invalid commit; EkzD's responsibility is to refuse verification or acceptance when that Git-visible result violates the measurable contract.

Free-text authority, source, and constraint entries remain instructions for the AI or human operator. The V1 verifier mechanically enforces configuration integrity, local session-state integrity, Git index visibility, historical and current path scope, session commit budget, configured commands, exact-state binding, and explicit acceptance.

## Acceptance

Acceptance is a separate stage from verification.

`ekzd finish --accept` must reject the task unless all of the following are true:

1. an active local, untracked session exists;
2. the project harness configuration still matches the digest captured at session start;
3. configured verification completed successfully;
4. the active session still satisfies its original commit budget;
5. tracked paths do not use `assume-unchanged` or `skip-worktree` to suppress Git visibility;
6. the verified project configuration digest is unchanged;
7. Git HEAD is unchanged from the verified state;
8. the branch is unchanged from the verified state;
9. the worktree status is unchanged from the verified state;
10. the Git-visible state fingerprint is unchanged from the verified state;
11. historical and current scope still pass; and
12. the operator explicitly supplies `--accept` after the required review.

`--accept` is an operator attestation. V1 does not authenticate the operator's identity or cryptographically prove that a particular human performed the review.

Acceptance requires the current Git-visible tracked, staged, unstaged, and untracked project state to exactly match the state bound to the successful verification. If the current state differs, the prior verification cannot be accepted and verification must be rerun.

Git-ignored files are outside V1's exact-state fingerprint. A project that needs an ignored file to affect acceptance must check that requirement through a configured verification command. V1 does not recursively hash every ignored file by default.

## What this standard does not prove

Passing verification does not prove that requirements were interpreted correctly, that tests are complete, that free-text project guidance was followed semantically, or that a change is desirable. Acceptance criteria may include product and code-quality judgments that require human review.

EkzD therefore records explicit operator acceptance rather than treating a successful command as automatic authorization to merge or deploy.

## V1 portability rule

Project configuration must use repository-relative paths and command arrays. EkzD runs commands directly without a shell. This keeps command behavior deterministic and avoids shell interpolation becoming part of the harness contract.

Future schema versions may add stronger evidence or policy gates, but V1 should remain backward-compatible within `schema_version = 1` or require an explicit schema increment.
