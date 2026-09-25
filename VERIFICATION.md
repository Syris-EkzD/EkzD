# EkzD Structural Verification and Acceptance Standard

EkzD is a deterministic structural trust layer. It verifies authority and exact-candidate evidence;
external CI verifies machine-checkable project correctness; maintainers and humans retain semantic
review and merge authority.

Old project/task/contract authority formats are unsupported. Refreeze with the current schemas.

## Candidate evidence

EkzD uses one raw candidate snapshot primitive for worker and maintainer evidence. Its binding
includes HEAD, branch, stage-zero index blob identities and modes, tracked working-file bytes and
modes (including deletions), symlink values without following targets, and non-ignored untracked
paths and contents. Paths are byte-preserving and fields are length-delimited. Git textconv and
external diff programs do not define evidence or scope.

Final cleanliness requires raw working files and executable modes to match the index, and the index
to match HEAD. Checkout transformations do not count as clean merely because normal Git status hides
them. Development checking may bind dirty candidate files, but only when all changed and historical
paths comply with frozen scope.

Submodules, shallow repositories, sparse checkout, replacement refs/grafts, hidden index flags,
active tracked content filters, conflicted stages, special/nested candidate paths, detached final
candidates, and in-progress merge/rebase/cherry-pick/revert/bisect operations are rejected.

Snapshots assume quiescent local work and do not defend against hostile same-user processes or
transient modify-and-restore activity. Ignored files and external symlink targets are outside the
binding.

## Frozen authority

At `ekzd start`, EkzD requires a clean supported baseline and committed clean project policy. It
combines closed project schema v4 and task schema v3 into canonical contract schema v3. The frozen
contract contains:

- exact baseline and declared implementation branch;
- project name and normalized project-policy SHA-256;
- objective;
- sorted unique include, exclude, and protected patterns;
- baseline source/reference paths;
- ordered acceptance criteria and additive project/task guidance;
- fixed 50-commit safety ceiling; and
- exact EkzD version/build SHA-256.

No schema accepts `verification` or `readiness`. The frozen contract contains no arbitrary command,
toolchain, dependency, test, lint, formatting, analysis, or build plan.

Canonical JSON is UTF-8 with sorted object keys, compact separators, unescaped Unicode, and exactly
one trailing LF. Its SHA-256 is the contract ID. This checksum is an integrity identity, not a
signature. Worker and maintainer checks use only frozen authority; mutable authoring files are never
reinterpreted after freezing.

Project protections and exclusions cannot be weakened by task fields. Project and task guidance
append. Acceptance and guidance remain instructions for humans/workers, not mechanically evaluated
semantic predicates. Declared sources are validated as regular files at the baseline, not required
to survive in the implementation worktree when scope permits deletion or renaming.

## Structural checking modes

All modes validate contract identity, exact pinned EkzD identity, a supported Linux platform,
Python 3.11+ runtime support, Git availability, supported repository state, local baseline existence,
branch validity, baseline
ancestry, fixed commit ceiling, historical/current scope, and candidate/handoff authority binding as
appropriate.

`python3 run.py prepare` additionally requires the candidate to be at the exact clean frozen
baseline on the declared branch. These are EkzD prerequisites. It does not inspect or install
project-specific tools, packages, services, databases, SDKs, or frameworks.

Development `python3 run.py check` allows legitimate dirty work and verifies that the exact current
Git-visible state remains within frozen structural authority.

`python3 run.py check --final` and maintainer `ekzd verify` require a clean fully committed candidate
on the declared implementation branch and bind PASS to that exact candidate and contract.

The shared checker retains its initial candidate binding through all structural operations and
recaptures before returning. Authority guards bind worker checks to exact contract/handoff bytes and
maintainer checks to exact local session bytes. A changed candidate, runtime, handoff, contract, or
session fails the attempt instead of being silently rebound.

No structural mode executes project-specific commands.

## Scope and historical evidence

Task `include` is a required allowlist. Effective `exclude` is the union of project and task
exclusions. Effective `protected` adds project protections to `.ekzd/project.toml`, `.ekzd/local/`,
and legacy `.ekzd/session.json`. Exclusion/protection wins over inclusion.

Directory patterns ending in `/` match that directory and descendants. Other patterns use
case-sensitive Python `fnmatch`; `*` may match `/`, and `**` is not a separate recursive language.
Paths must be repository-relative without traversal.

Scope includes every path touched by every commit between baseline and HEAD, including merge/side
history and reverted changes, plus current staged, unstaged, deleted, and non-ignored untracked
candidate paths. Renames expose source and destination. A later revert cannot erase an unauthorized
historical change. The baseline must remain an ancestor, and all commits reachable from HEAD but not
baseline count toward the fixed 50-commit ceiling.

## Portable handoff and runtime identity

The portable archive contains canonical contract and instructions, a minimal launcher, manifest,
and exact pinned EkzD source. Archive/member ordering, timestamps, modes, paths, and bytes are
deterministic. The manifest hashes each payload member. The launcher re-executes Python with `-I -S`,
loads only the bundled runtime, verifies import location/build identity, and validates payload
integrity before import and at checking boundaries. Installed packages, `PYTHONPATH`, and candidate
EkzD source cannot replace the pinned runtime.

The worker needs only EkzD prerequisites—supported Linux, Python 3.11+, Git—the repository, and the
portable handoff. External CI is responsible for provisioning every project-specific dependency and
toolchain.

## Independent maintainer evidence and acceptance

`.ekzd/local/session.json` atomically stores the contract, contract ID, handoff identity, status,
verification, and acceptance evidence. `.ekzd/local/` must remain ignored and untracked. A Linux
advisory lock serializes lifecycle changes.

`ekzd verify` clears earlier success before each attempt and independently performs final structural
checking against the maintainer's frozen authority. Its record binds the exact contract ID and raw
candidate binding. Any candidate or authority change makes that record stale.

`ekzd finish --accept` requires explicit `--accept`, active valid frozen state, fresh successful
maintainer structural verification for the exact candidate/contract, and one more structural and
binding check before recording the accepted HEAD. EkzD does not inspect CI state. The maintainer must
review external CI evidence separately, and the human retains the merge decision.

## Responsibility boundary

A structural PASS means only:

> This exact candidate complies with the frozen EkzD authority.

It does not prove builds, tests, lint, formatting, static analysis, dependencies, acceptance
criteria, semantic correctness, architecture, code quality, security, environment reproducibility,
or review quality.

External CI or an equivalent system must run the required machine-checkable project correctness
work for the exact candidate. CI failure loops back to scoped worker fixes, a new committed candidate,
EkzD structural rechecking, republishing, and CI again. EkzD never queries or controls CI.

The checksum is not authentication. Replacement of the launcher and manifest together by a hostile
operator, a compromised Python or Git binary, malicious startup behavior before `run.py`, and
hostile same-user interference are outside the trust model. Human/code review and explicit merge
authority remain external and mandatory.
