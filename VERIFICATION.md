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
- the active session's commit count and configured maximum;
- each verification command, working directory, exit code, and bounded output.

Verification fails closed when configuration is invalid or changed after session start, the session commit budget is exceeded, scope is violated, a working directory escapes the repository, a command times out, or any configured command fails.

Scope is checked both before and after the configured verification commands run. This prevents a successful verifier from silently creating or modifying Git-visible files outside the declared session scope. A verifier-created scope violation invalidates verification even if every configured command exited successfully.

## Immutable session contract

When `ekzd start` creates a session, it records the digest of the complete `.ekzd/project.toml` file.

Verification and acceptance require the current configuration to match that starting digest. Scope, authority, session policy, acceptance criteria, verification commands, and other harness settings therefore cannot be loosened or otherwise changed during an active session. Change the configuration only after aborting or finishing the current session, then start a fresh session.

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

Session state (`.ekzd/session.json`) is excluded from changed-path evaluation because it is harness metadata, not project output.

## AI-assisted operating model

EkzD does not need to be the code generator to enforce this standard.

In the recommended V1 workflow, the local EkzD session defines the contract, `ekzd context --json` is handed to a prompt-generation/planning session, and a separate coding session implements the resulting prompt through GitHub. The resulting branch is then pulled into the environment where EkzD is running and verified against the original local contract.

This means V1 provides deterministic post-work enforcement rather than real-time control over a remote AI session. A remote code generator can technically create an invalid commit; EkzD's responsibility is to refuse verification or acceptance when that Git-visible result violates the measurable contract.

Free-text authority, source, and constraint entries remain instructions for the AI or human operator. The V1 verifier mechanically enforces configuration integrity, path scope, session commit budget, configured commands, exact-state binding, and explicit acceptance.

## Acceptance

Acceptance is a separate stage from verification.

`ekzd finish --accept` must reject the task unless all of the following are true:

1. an active session exists;
2. the project harness configuration still matches the digest captured at session start;
3. configured verification completed successfully;
4. the active session still satisfies its original commit budget;
5. the verified project configuration digest is unchanged;
6. Git HEAD is unchanged;
7. the branch is unchanged;
8. the worktree status is unchanged;
9. the Git-visible state fingerprint is unchanged;
10. scope still passes; and
11. explicit human approval is supplied with `--accept`.

If any Git-visible tracked, staged, unstaged, or untracked project state changes after verification, the prior result becomes stale and verification must be rerun.

Git-ignored files are outside V1's exact-state fingerprint. A project that needs an ignored file to affect acceptance must check that requirement through a configured verification command. V1 does not recursively hash every ignored file by default.

## What this standard does not prove

Passing verification does not prove that requirements were interpreted correctly, that tests are complete, that free-text project guidance was followed semantically, or that a change is desirable. Acceptance criteria may include product and code-quality judgments that require human review.

EkzD therefore records explicit approval rather than treating a successful command as automatic authorization to merge or deploy.

## V1 portability rule

Project configuration must use repository-relative paths and command arrays. EkzD runs commands directly without a shell. This keeps command behavior deterministic and avoids shell interpolation becoming part of the harness contract.

Future schema versions may add stronger evidence or policy gates, but V1 should remain backward-compatible within `schema_version = 1` or require an explicit schema increment.
