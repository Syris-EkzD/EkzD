# EkzD Verification and Acceptance Standard

This document defines the V1 acceptance contract. It is intentionally stricter than simply running tests once.

## Verification

A verification result is valid only for the exact state that produced it.

EkzD records:

- the current Git commit (`HEAD`);
- the current branch;
- the complete porcelain worktree status;
- the project configuration digest;
- the changed paths considered for scope enforcement;
- each verification command, working directory, exit code, and bounded output.

Verification fails closed when configuration is invalid, scope is violated, a working directory escapes the repository, a command times out, or any configured command fails.

## Scope

`scope.include` is an allowlist when non-empty. A changed path must match at least one include pattern.

`scope.exclude` is always a denylist. A changed path matching an exclude pattern blocks verification even when it also matches an include pattern.

Directory patterns ending in `/` match that directory and its descendants. Other patterns use case-sensitive glob matching.

Session state (`.ekzd/session.json`) is excluded from changed-path evaluation because it is harness metadata, not project output.

## Acceptance

Acceptance is a separate stage from verification.

`ekzd finish --accept` must reject the task unless all of the following are true:

1. an active session exists;
2. configured verification completed successfully;
3. the project configuration digest is unchanged;
4. Git HEAD is unchanged;
5. the branch is unchanged;
6. the worktree status is unchanged;
7. scope still passes; and
8. explicit human approval is supplied with `--accept`.

If any project file, configuration entry, branch, commit, or worktree state changes after verification, the prior result becomes stale and verification must be rerun.

## What this standard does not prove

Passing verification does not prove that requirements were interpreted correctly, that tests are complete, or that a change is desirable. Acceptance criteria may include semantic or product judgments that require human review.

EkzD therefore records explicit approval rather than treating a successful command as automatic authorization to merge or deploy.

## V1 portability rule

Project configuration must use repository-relative paths and command arrays. EkzD runs commands directly without a shell. This keeps command behavior deterministic and avoids shell interpolation becoming part of the harness contract.

Future schema versions may add stronger evidence or policy gates, but V1 should remain backward-compatible within `schema_version = 1` or require an explicit schema increment.
