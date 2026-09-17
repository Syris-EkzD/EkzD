# EkzD Verification and Acceptance Standard

This document describes the current frozen-contract workflow. Old project/task/session authority formats are unsupported; start a new task with the current schemas.

## Verification

EkzD uses one raw candidate snapshot primitive and one verification-command evaluator for worker and maintainer evidence. The binding includes HEAD, branch, stage-zero index blob identities and modes, tracked working-file bytes/modes (including deletions), symlink values without following targets, and non-ignored untracked paths/contents. Paths are byte-preserving and fields are length-delimited. Conflicted index stages are rejected. Textconv and external diff programs do not define candidate evidence or scope. Final cleanliness additionally requires raw working-file content and executable modes to match the index, and the index to match HEAD; Git checkout transformations do not qualify as clean merely because Git status hides them.

Both `ekzd verify` and `ekzd check --final` require the declared implementation branch, a fully committed clean candidate, and no conflicts or in-progress merge/rebase/cherry-pick/revert/bisect operation. Submodules, shallow repositories, sparse checkout, replacement refs/grafts, hidden index flags, active tracked content filters, and special/nested candidate paths are rejected. Worker development checking may include dirty candidate files but does not relax unsupported-state checks.

The initial candidate is retained throughout verification. Each command is bracketed by snapshots, and completion compares against that same initial binding. Persistent mutation or lost candidate trust fails the attempt, stops further command execution, and leaves mutations visible. Ordinary nonzero command failures may continue to later checks while repository trust remains intact, so independent failures can be reported together. Maintainer verification clears prior success before executing commands; acceptance requires the new candidate binding and rechecks it before recording the accepted HEAD. Older verification evidence must be regenerated.

Sources are validated as regular files at the frozen baseline, not as files required to survive in the implementation worktree. Source deletion or renaming remains subject to ordinary scope rules.

Verification commands run directly from argv arrays, without a shell, through the shared evaluator. On Linux each command is launched in its own process group. If a command times out, EkzD terminates that process group, waits briefly, sends a kill signal if needed, and then recaptures candidate state before deciding whether evaluation can continue. Keyboard interruption performs the same owned-process cleanup before propagating. This does not contain intentionally daemonized descendants that deliberately escape the process group.

Verifier stdout and stderr are retained with a fixed bound. EkzD keeps diagnostic output from both streams, marks truncation explicitly, and decodes invalid UTF-8 with replacement characters rather than crashing or buffering unbounded output.

Snapshots assume quiescent local work and do not defend against hostile concurrent processes or transient modify-and-restore activity. Ignored files, external link targets, toolchains, and services remain outside the binding. EkzD does not provide environment reproducibility.

## Frozen authority

At `ekzd start task.toml`, EkzD validates a clean supported baseline and the committed durable `.ekzd/project.toml`, then composes it with disposable task intent and the executing runtime identity. Project schema 2 and task-authoring schema 1 are closed TOML objects. See [README.md](README.md#project-setup-and-repeated-tasks) for their complete shapes and composition rules.

The result is one contract-version-1 JSON object. It includes baseline, branch, project name and normalized configuration digest, objective, resolved include/exclude/protected patterns, baseline sources, acceptance, additive guidance, selected budget, full ordered verification plan and exact EkzD identity. It does not refer back to the original task file. Neither worker nor maintainer reconstructs authority from mutable inputs after freezing.

Canonical JSON is UTF-8 with sorted object keys, compact separators, unescaped Unicode, and exactly one trailing LF. Scope patterns are sorted/unique; ordered prose and command lists retain order. The SHA-256 of these bytes is the contract ID, stored alongside the contract in local metadata. It is a checksum, not a signature. JSON export accepts no legacy TOML manifests or alternate contract versions. Worker input must use the exact canonical encoding emitted by `ekzd contract`.

Project protections/exclusions cannot be weakened by task fields. Task commands append to required project commands, never replace them. Project and task guidance append; acceptance/guidance remain human/AI instructions rather than semantically checked predicates. Task budget overrides the project default (20 if omitted); budgets are positive integers with no universal maximum.

The exact version/build SHA-256 is frozen at start and required for worker checking, maintainer verification and acceptance. The existing source identity algorithm is unchanged: sorted package-relative Python paths with normalized source line endings, independent of installation location and Git metadata. Worker preparation and delivery of the pinned runtime remain manual. Remotes are not part of authority and are not captured.

## Lifecycle evidence

`.ekzd/local/session.json` atomically stores the contract, contract ID, status and lifecycle evidence. Files under `.ekzd/local/` must remain ignored/untracked. A Linux advisory lock serializes lifecycle mutations; state publication uses a same-directory temporary file, fsync and atomic replacement.

The maintainer adapter validates the stored contract ID, clears earlier success before a new verification attempt, and guards the exact session bytes at evaluator boundaries. The stateless worker adapter guards the supplied contract bytes instead; it does not read maintainer session state. Changed authority stops subsequent commands. The shared checker retains the initial candidate throughout structural checks and verifier execution, including between those boundaries.

Verification records the candidate binding, contract ID and structured results. `finish --accept` requires successful evidence for that same candidate and contract, rechecks authority and final state, and records the exact accepted HEAD/candidate/contract ID. It does not rerun commands. Changing authority requires abort, edit and refreeze; a new session clears all prior evidence. Finished/aborted records remain local until the next start replaces them. There is no acceptance-history database or automatic migration.

Export and prompt generation read the frozen record, even if the original task or current policy is edited. This exposes the original authority rather than silently replacing it. A candidate touching protected policy still fails evaluation. Exported copies are transport artifacts, not another mutable source of authority for the maintainer.

## Scope and historical evidence

Task `include` is a required allowlist. Resolved `exclude` is the union of project and task exclusions; `protected` adds permanent project protections to `.ekzd/project.toml`, `.ekzd/local/`, and the legacy `.ekzd/session.json`. Exclusion/protection always wins over inclusion.

Directory patterns ending in `/` match that directory and descendants. Other patterns use case-sensitive Python fnmatch matching, where `*` may match `/`; `**` is not a separate recursive matching language. Paths must be repository-relative without traversal components.

Scope includes all paths touched in commits between baseline and HEAD, including merge/side history and reverted changes, plus current candidate changes. Git paths are NUL-delimited; renames are inspected as source and destination changes. A later revert cannot erase unauthorized history. Commit counts include all commits reachable from HEAD but not baseline, and the baseline must remain an ancestor.

An implementation may delete or rename a declared source if scope permits; source existence is checked at baseline. Ignored drafts and lifecycle files are not implementation changes, but tracking them or touching protected local paths in candidate history is rejected.

## Acceptance and limitations

Acceptance requires:

1. an active valid local record and explicit `--accept`;
2. matching frozen runtime identity;
3. the declared branch, supported repository state, clean committed candidate and baseline ancestry;
4. frozen scope, protections and commit budget satisfied;
5. successful maintainer verification bound to the exact contract ID and initial candidate;
6. those bindings still unchanged before acceptance is written.

The checksum is not authentication. EkzD assumes the maintainer supplies trusted contract bytes to the worker; the maintainer independently verifies against their own frozen record. An altered worker contract or forged worker PASS cannot replace this local acceptance evidence.

Passing does not prove semantic correctness, complete tests, compliance with prose guidance, environment reproducibility or human review. Ignored files (including build artifacts), external symlink targets, toolchains, services and environment variables are not candidate-bound. A verifier consulting them does not add them to the snapshot. Persistent changes at evaluation boundaries are detected; transient modify/restore activity and hostile same-user control are outside the model.

Linux is the required platform. Process groups handle ordinary verifier descendants, not intentionally escaped daemon processes. Required unavailable verification tools remain blocking; EkzD does not install them. Human review and merge authority remain external to the harness.
