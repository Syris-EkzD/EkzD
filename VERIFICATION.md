# EkzD Verification and Acceptance Standard

This document defines the V1 acceptance contract. It is intentionally stricter than simply running tests once.

## Verification

A verification result is valid only for the exact Git-visible state that produced it and only under the canonical committed harness configuration captured when the session started.

EkzD records:

- the current Git commit (`HEAD`);
- the current branch;
- the complete porcelain worktree status;
- the Git-visible state fingerprint;
- the project configuration digest;
- the changed paths considered for scope enforcement;
- the active session's final commit count and configured maximum;
- each verification command, working directory, exit code, and bounded output.

Verification fails closed when configuration is invalid, uncommitted, staged, unstaged, changed after session start, or historically touched during the active session; when the session commit budget is exceeded; when scope is violated; when tracked paths use Git index flags that suppress normal change visibility; when local session state is tracked or changes during verification; when a working directory escapes the repository; when a command times out; when a configured command cannot be launched; or when any configured command fails.

Before commands run, EkzD validates the ready project configuration, requires the canonical `.ekzd/project.toml` to remain committed and clean, validates the active session contract, protected harness history, commit budget, Git index visibility, and changed-path scope, and pins the active local session state for the verification attempt. After the configured commands finish, EkzD reloads the ready configuration, again requires the canonical project contract to be committed and clean, requires the local session state to remain untracked and unchanged, and rechecks the frozen configuration digest, protected harness history, commit budget, Git index visibility, and scope against the original pinned session state before recording verification evidence. This prevents a verifier from redefining the session baseline, staging a different harness contract, silently changing contextual instructions, publishing local session metadata through accepted history, exceeding the session budget, hiding tracked changes with Git index flags, or creating Git-visible changes outside the declared scope while still producing a green verification result.

Changed-path discovery is rename-safe and NUL-delimited. EkzD disables Git rename collapsing for scope evaluation so both sides of a move are evaluated independently, and it parses Git paths without relying on newline-delimited or quoted output. For committed work, scope is historical rather than net-only: EkzD unions the paths touched by every commit reachable in the active session range from the recorded starting `HEAD` through the current `HEAD`. A path therefore remains part of scope evaluation even when a later session commit restores its contents to the starting version.

## Canonical project contract

`.ekzd/project.toml` is V1's canonical project contract. Before `ekzd start`, it must already exist in `HEAD` and must have no staged or unstaged changes. V1 intentionally does not support starting a session from an untracked, staged-only, or locally dirty project contract.

The canonical contract must be a regular tracked file; symlinked `.ekzd/project.toml` contracts are not supported because Git would otherwise version only the link target string rather than the TOML contents EkzD reads.

This gives the single-operator workflow one reproducible definition of scope, authority, sources, session policy, acceptance criteria, and verification commands. HEAD, the Git index, the working tree, `ekzd context --json`, local verification, and CI are not allowed to silently disagree about which project contract is authoritative.

When `ekzd start` creates a session, it records the digest of the complete committed `.ekzd/project.toml` file. While that session is active, `ekzd context`, `ekzd verify`, and `ekzd finish --accept` require the file to remain clean and to match the recorded digest. If configuration must change, abort or finish the session first, edit and commit the configuration outside an active session, then start a fresh session.

## Protected harness history

V1 protects exactly two trust-critical paths from active-session commit history:

- `.ekzd/project.toml`;
- `.ekzd/session.json`.

This protection is independent of `scope.include` and `scope.exclude`. A repo-wide project scope such as `include = ["*"]` does not authorize committing either protected path while a session is active. If either path appears in any commit reachable in the active session range, that session is invalid even when a later commit restores or untracks the file.

The protected set is intentionally limited to these two files rather than the entire `.ekzd/` directory. Future non-trust-critical EkzD artifacts may therefore evolve without inheriting a blanket history prohibition. Adding another trust-critical harness file in a future version requires explicitly adding it to the protected set.

The historical rule is stricter than the current-byte digest alone: committing `.ekzd/project.toml` during the session permanently invalidates that session even if a later commit restores the exact original bytes.

## Session-state integrity

`.ekzd/session.json` is trusted operator-local harness metadata. It must remain untracked by Git and must never appear in active-session commit history. EkzD refuses to read or write session state while the path is currently tracked, and protected-history enforcement also rejects a session where the file was committed and later removed from tracking.

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

Protected harness history and canonical-config cleanliness are evaluated separately from project scope. `.ekzd/project.toml` cannot be dirty, staged, uncommitted, or present in active-session commits regardless of project scope. `.ekzd/session.json` cannot be tracked or appear in active-session commits. Only the current untracked `.ekzd/session.json` file is omitted from ordinary project changed-path evaluation because it is required local harness metadata.

EkzD rejects tracked paths marked with Git's `assume-unchanged` or `skip-worktree` index flags because those flags intentionally suppress ordinary status/diff visibility and would weaken deterministic scope and exact-state checks. V1 therefore does not support operating inside a sparse-checkout state that depends on `skip-worktree`; clear those flags or use a normal checkout before starting or verifying an EkzD session.

## Context integrity

`ekzd context` and `ekzd context --json` are part of the active contract, not merely convenience renderers. When a session is active, EkzD refuses to emit context unless the committed `.ekzd/project.toml` is still clean, still matches the session-start digest, and has not appeared in active-session commit history.

This matters because sources, authority, and free-text constraints are intentionally contextual rather than semantically enforced by V1. The prompt-generator must therefore receive the same frozen contract that later verification and acceptance use.

## AI-assisted operating model

EkzD does not need to be the code generator to enforce this standard.

In the recommended V1 workflow, the local EkzD session defines the contract, `ekzd context --json` is handed to a prompt-generation/planning session, and a separate coding session implements the resulting prompt through GitHub. The resulting branch is then pulled into the environment where EkzD is running and verified against the original local contract.

This means V1 provides deterministic post-work enforcement rather than real-time control over a remote AI session. A remote code generator can technically create an invalid commit; EkzD's responsibility is to refuse verification or acceptance when that Git-visible result violates the measurable contract.

Free-text authority, source, and constraint entries remain instructions for the AI or human operator. The V1 verifier mechanically enforces canonical configuration integrity, protected harness-history integrity, local session-state integrity, Git index visibility, historical and current path scope, session commit budget, configured commands, exact-state binding, and explicit acceptance.

## Acceptance

Acceptance is a separate stage from verification.

`ekzd finish --accept` must reject the task unless all of the following are true:

1. an active local, untracked session exists;
2. `.ekzd/project.toml` remains committed, clean, and identical to the digest captured at session start;
3. neither `.ekzd/project.toml` nor `.ekzd/session.json` appears in active-session commit history;
4. configured verification completed successfully;
5. the active session still satisfies its original commit budget;
6. tracked paths do not use `assume-unchanged` or `skip-worktree` to suppress Git visibility;
7. the verified project configuration digest is unchanged;
8. Git HEAD is unchanged from the verified state;
9. the branch is unchanged from the verified state;
10. the worktree status is unchanged from the verified state;
11. the Git-visible state fingerprint is unchanged from the verified state;
12. historical and current scope still pass; and
13. the operator explicitly supplies `--accept` after the required review.

`--accept` is an operator attestation. V1 does not authenticate the operator's identity or cryptographically prove that a particular human performed the review.

Acceptance requires the current Git-visible tracked, staged, unstaged, and untracked project state to exactly match the state bound to the successful verification. If the current state differs, the prior verification cannot be accepted and verification must be rerun.

Git-ignored files are outside V1's exact-state fingerprint. A project that needs an ignored file to affect acceptance must check that requirement through a configured verification command. V1 does not recursively hash every ignored file by default.

## What this standard does not prove

Passing verification does not prove that requirements were interpreted correctly, that tests are complete, that free-text project guidance was followed semantically, or that a change is desirable. Acceptance criteria may include product and code-quality judgments that require human review.

EkzD therefore records explicit operator acceptance rather than treating a successful command as automatic authorization to merge or deploy.

## V1 portability rule

Project configuration must use repository-relative paths and command arrays. EkzD runs commands directly without a shell. This keeps command behavior deterministic and avoids shell interpolation becoming part of the harness contract.

Future schema versions may add stronger evidence or policy gates, but V1 should remain backward-compatible within `schema_version = 1` or require an explicit schema increment.
