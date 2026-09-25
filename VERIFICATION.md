# EkzD Structural Verification and Acceptance Standard

EkzD verifies structural evidence, not project correctness or human judgment.

## Structural verification

Worker `check` and maintainer `ekzd verify` share the same frozen-contract structural checker.
It does not execute project-defined commands.

The candidate binding includes HEAD, branch, stage-zero index identities/modes, tracked working
bytes/modes, symlink values without following targets, and non-ignored untracked paths/contents.
Conflicted index stages are rejected. Final verification requires a clean fully committed candidate
on the declared implementation branch.

EkzD rejects unsupported repository states including shallow history, sparse checkout, replacement
refs/grafts, hidden index flags, active tracked content filters, submodules, nested repositories,
and in-progress merge/rebase/cherry-pick/revert/bisect operations.

Scope covers every path touched in commits between baseline and HEAD, including merge/side history
and reverted changes, plus current candidate changes. Exclusion and protection override inclusion.
A later revert cannot erase unauthorized history.

Commit count is the number of commits reachable from HEAD but not the frozen baseline. The baseline
must remain an ancestor and the fixed safety ceiling is 50.

The checker captures the candidate before structural evaluation and captures it again before
returning. If the candidate or frozen authority changes across those boundaries, the attempt fails
and remains bound to the initial evidence.

## What EkzD does not execute

Project and task authoring schemas do not accept readiness or verification commands. EkzD does not
run formatters, linters, static analyzers, tests, builds, package managers, framework SDKs,
deployment checks, or other dependency-bound tooling.

Those checks belong to CI or another external verifier. This keeps portable worker requirements
limited to EkzD's own prerequisites: Linux, Python 3.11+, Git, the repository, and the handoff.

A structural PASS therefore means only that the candidate obeys frozen implementation authority. It
does not mean the project builds or tests pass.

## Frozen authority

At `ekzd start`, EkzD validates a clean supported baseline and committed durable policy, then
composes it with disposable task intent and the executing runtime identity.

Current closed authoring schemas:

- project schema 4;
- task schema 3;
- frozen contract schema 3.

The contract contains the exact baseline, branch, project/config identity, objective, resolved
include/exclude/protected scope, baseline sources, acceptance criteria, additive guidance, fixed
50-commit ceiling, and exact EkzD runtime identity. It contains no project command plan.

Canonical JSON is UTF-8 with sorted keys, compact separators, unescaped Unicode, and exactly one
trailing LF. The SHA-256 of those bytes is the contract ID. It is an integrity checksum, not a
signature.

The portable handoff pins the runtime source and hashes every payload member. The launcher isolates
ordinary Python package resolution and verifies handoff/runtime identity before structural checking.

## Preparation

`run.py prepare` verifies only EkzD prerequisites and frozen starting state:

- supported Linux/Python/Git environment;
- intact handoff and pinned runtime;
- available baseline commit;
- declared implementation branch;
- clean candidate exactly at the frozen baseline;
- frozen structural authority.

It does not probe project SDKs or dependencies.

## Worker completion rule

Workers should run structural checks while implementing and `check --final` on the clean committed
candidate. After an EkzD final PASS, the candidate must be published through the external workflow
and required CI must pass for that exact published commit before the implementation session is
reported as successfully complete.

If CI fails, the worker should use the CI evidence to fix the implementation within frozen
authority, rerun EkzD, publish the new exact candidate, and wait for CI again. If CI cannot be
observed or required checks cannot run, that is a blocker to report rather than a successful
completion.

EkzD does not itself query GitHub or CI; correlation of EkzD evidence and CI status belongs to the
maintainer/orchestrator.

## Lifecycle evidence

`.ekzd/local/session.json` stores the frozen contract, contract ID, lifecycle status, handoff
identity, verification evidence, and acceptance evidence. It remains ignored/untracked.

Maintainer verification clears prior success before a new attempt, binds successful evidence to the
exact contract and candidate, and refuses stale acceptance. `finish --accept` requires explicit
human approval and matching structural verification evidence; it does not rerun checks or inspect
CI.

Changing authority requires abort/edit/refreeze. Finished and aborted records remain local until
the next start replaces them.

## Acceptance

EkzD acceptance requires:

1. an active valid local record and explicit `--accept`;
2. matching frozen runtime identity;
3. declared branch and supported repository state;
4. a clean committed final candidate;
5. baseline ancestry, scope, protections, and fixed commit ceiling satisfied;
6. successful maintainer structural verification bound to the exact contract/candidate;
7. unchanged bindings before acceptance is written.

The surrounding workflow should additionally require CI success for the same exact published
candidate before merge.

## Limits

EkzD does not prove semantic correctness, complete tests, build success, architecture quality,
maintainability, environment reproducibility, or CI status. Ignored files, external symlink targets,
toolchains, services, and hostile same-user interference are outside the binding.

Human review and merge authority remain external to the harness.
