# Workflow Manager V0

**Status: frozen after V0.** Workflow Manager is a completed WhiteTree experiment,
preserved for historical and technical reference. It is not WhiteTree's central
orchestration platform or part of the standard development path.

WM-V0-01 processes WhiteTree Mind’s reviewed Horus agent proposal into a
non-canonical profile candidate. Python 3.10+ and Git are the only dependencies.
WM-V0-02 separately promotes a reviewed candidate with explicit approval.
There is no service, agent framework, or account integration.

## Current direction

The canonical architecture and decisions live in these files, relative to the
WhiteTree root (in the separate Mind repository):

- `Mind/Vault/Projects/WhiteTree/Overview.md`
- `Mind/Vault/Projects/WhiteTree/Governance.md`
- `Mind/Vault/Technology/Tools/AI-Development-Workflow.md`
- `Mind/Vault/Projects/WorkflowManager/Overview.md`
- `Mind/Vault/Projects/WorkflowManager/Tasks.md`

WhiteTree uses **Mind + Work + ChatGPT/Work + Codex + Git/CI**. EkzD and Horus
are domain role or skill definitions, not standalone autonomous systems. Ordinary
documentation work uses scoped edits, Git review, and an approved commit; it does
not require this tool's candidate-generation and promotion workflow.

The task decisions are:

- **WM-V0-01:** completed candidate generation and human review.
- **WM-V0-02:** completed explicit canonical promotion.
- **WM-V1-01:** generalizing workflow definitions is paused pending evidence of need.
- **WM-V1-02:** automatic Codex invocation is cancelled pending evidence of need.

V0 used a controlled current-session reasoning handoff. It did not autonomously
invoke Codex. Historical V1 objectives do not authorize further development.
Reconsideration requires a real recurring workflow, observed friction, a bounded
requirement, evidence that established tools cannot meet it, and explicit approval.
Any future solution should begin with the smallest missing component and need not
belong in Workflow Manager.

Preserve the V0 source, tests, original Mind proposal and candidate, and useful
local reports. Maintenance is limited to keeping the historical record clear or
addressing a concrete safety issue. Private run and promotion artifacts remain
local and excluded from Git.

## Historical V0 usage

The commands below document the retained implementation, not an instruction to
rerun the completed workflow. The canonical Horus profile already exists and has
since been revised in Mind. Generation refuses an existing candidate; promotion
refuses an existing canonical destination and rejects changed source inputs.
Do not remove preserved files to replay the example. Use the isolated test
fixtures for verification.

From `~/WhiteTree/Work/Tools/workflow-manager`:

```sh
python3 wm.py prepare --run horus-001
# The current AI/Codex reasoning session reads .runs/horus-001/prompt.json
# and writes the schema-conforming answer to .runs/horus-001/response.json.
python3 wm.py finish --run horus-001
```

The configured reasoning mode is **current-session-handoff**. The CLI builds the
complete structured prompt automatically; the active reasoning assistant consumes
it and supplies the response without the user copying project context. This is an
explicit two-step integration, not an unattended model invocation. No API key,
provider SDK, nested Codex process, shell command configuration, or external account
is used. If no reasoning session supplies a response, finishing fails closed.

`--root /absolute/path/to/WhiteTree` supports a relocated WhiteTree. This tool must
remain inside that root. Run names are restricted to letters, digits, `_`, and `-`.
Use a new name for each attempt; finalized runs cannot be replayed. Existing
candidates are never overwritten. Review and separately resolve an existing
candidate before rerunning; the tool never deletes it.

## Required paths

Relative to `~/WhiteTree`, all nine context files must exist as non-empty UTF-8,
single-link regular files (maximum 200 KB each), without symlink ancestors:

- `Mind/Vault/Projects/WorkflowManager/Overview.md`
- `Mind/Vault/Projects/WorkflowManager/Tasks.md`
- `Mind/Vault/Technology/Tools/AI-Development-Workflow.md`
- `Mind/Vault/Agents/EkzD/Profile.md`
- `Mind/Vault/Projects/WhiteTree/Overview.md`
- `Mind/Vault/Inbox/Horus-Agent-Proposal.md`
- `Mind/Vault/Projects/Horus/Overview.md`
- `Mind/Vault/Projects/Horus/Status.md`
- `Work/Projects/horus/README.md`

Only these contents enter the prompt. The candidate’s parent Inbox directory must
already exist. The sole Mind write target for generation is:

`Mind/Vault/Inbox/Horus-Agent-Profile-Candidate.md`

Local run artifacts live under `.runs/<name>/`: `prompt.json`, `state.json`,
`response.json`, `report.json`, and `report.md`. They are Git-ignored and may contain
private project context and filesystem paths; do not publish them without review.
Tool-generated files use mode 0600. No report embeds raw subprocess output or
unrelated file contents. JSON reports contain hashes, checks, observed changes,
failures, limitations, unresolved questions, and next actions. Exit 0 on `finish`
means ready for human review, never approved or canonical. Exit 1 means failure.

## Design and safety model

`wm.py` collects context/builds a prompt, validates a structured response, renders
a Markdown candidate, and audits/reports staging. A separate deterministic
promotion operation consumes the reviewed result without invoking AI. Tests use
disposable WhiteTree fixtures.

The response declares typed policy values covering every required boundary,
including progressive automation and separate promotion approval. The renderer
turns these into explicit mandatory profile rules. Narrative sections must be
substantive plain text and cite allowlisted context paths. Missing rules, altered
values, duplicate JSON keys, invalid structure, stale prompt hashes, suspicious
secret patterns, and malformed whitespace fail before staging. This is stronger
than accepting a bag of matching words, but does not establish semantic truth.

Before reasoning, the tool recursively snapshots **metadata** for every entry
under WhiteTree, including untracked/ignored files and Git internals. It never
opens unrelated files, secrets, databases, or repository source code. It records
type, permissions, owner, inode, size, nanosecond mtime/ctime and link count;
directories are checked for existence/type/ownership rather than timestamps.
Symlinks are recorded without following their targets. The five named local
artifacts are excluded. The audit also excludes changes to these known volatile
Obsidian UI/session files, defined in `VOLATILE_MIND_AUDIT_PATTERNS` in `wm.py`:

- `Mind/Vault/.obsidian/workspace.json`
- `Mind/Vault/.obsidian/workspace-mobile.json`
- `Mind/Vault/.obsidian/workspace.sync-conflict-*.json`
- `Mind/Vault/.obsidian/.syncthing.*.tmp`
- `Mind/Vault/.obsidian/graph.json`

These exclusions apply only to scope/change auditing, not input loading, write
permissions, content validation, or Git whitespace checks. Matching is case-sensitive
and path-segment scoped: wildcards cannot span directories. Only regular-file
changes are excluded; directory or symlink replacements remain detectable. Stable
configuration such as `app.json`, `appearance.json`, and `hotkeys.json`, and all
other unexpected `.obsidian` files remain audited. Snapshots retain their metadata;
only change comparisons filter the volatile paths.

Changes elsewhere in Work (including this tool’s source),
Mind, canonical agents, School, CSG, or Git metadata fail the run. Do not edit code,
run bytecode-producing tools, or sync files between prepare and finish.

Input hashes and the prompt hash bind the response to the gathered context. Scope
checks run before and after staging, including on validation failure. The output
uses exclusive creation, refuses symlinks and existing files, and is read back.
Mind’s working-tree and staged `git diff --check` must pass when Mind has `.git`;
candidate whitespace is separately checked because Git omits untracked candidates.
Pre-existing Mind whitespace errors block staging and are not silently repaired.
No Git index, commit, history, or remote writes are performed.

On failure, reports identify observed unexpected changes and the process exits
unsuccessfully. It does not attempt destructive rollback. A failed/partial candidate
must be inspected; its existence alone never means success.

## Limitations and approval boundary

- Snapshot comparison is an audit, **not an OS sandbox**. It cannot detect a
  create/delete operation entirely between snapshots, changes outside WhiteTree,
  writes through existing external symlink targets, or privileged tampering. The
  current reasoning session must honor its own filesystem and account boundaries.
  The CLI itself runs no model-supplied code and has no network operations.
- State and artifacts are trusted local files, not signed evidence against a
  malicious same-user process. There is no concurrent-writer lock or race-proof
  parent-directory handle traversal. Run in a quiet trusted checkout; concurrent
  changes fail the metadata audit where observable.
- Metadata scanning can be slow on large Work trees. It reads no secret values;
  the secret-pattern check on allowlisted text is only a heuristic, not a DLP tool.
- Narrative prose can contradict a declared policy or misinterpret a source.
  Deterministic checks cannot prove consistency, source support, completeness, or
  academic correctness. A human must read the candidate and its cited context.
- The proposal’s reviewed status is supplied by the operator, not inferred from
  its prose. This workflow does not approve proposals or change their status.
- Missing academic intake, privacy/retention conventions, and future integrations
  remain unresolved until confirmed. Documented software status is not live proof.

WM-V0-01 always stops before promotion. Success, a low risk rating, or an AI
recommendation does not authorize `Mind/Vault/Agents/Horus/Profile.md`. Only the
separate operation below can create that profile, after explicit human approval.
Neither workflow modifies School/CSG records or accesses accounts, commits, or pushes.

## Separate promotion after human review

After a human has reviewed and approved the exact candidate content, explicitly run:

```sh
python3 wm.py promote --run horus-rerun-02 --approve
```

This command is retained as a historical usage example. `--approve` asserts human
semantic approval of the candidate bound to that run. It is mandatory and valid
only with `promote`.
Generation success never supplies approval. Promotion invokes no AI reasoning.

WM-V0-02 requires an existing WM-V0-01 run with `ready_for_human_review`, no
failed or unexpected checks, and all required successful checks. It checks the
run configuration, original input hashes, prompt binding, structured response,
and exact rendered candidate. The candidate must be a non-empty, single-link
regular file without symlinks, and its exact UTF-8 byte hash must equal the
successful report's hash. Newline normalization is rejected. The original
proposal and every loaded input must remain unchanged. The canonical destination
must not exist, including as a directory or dangling symlink.

Promotion compares the old baseline with current WhiteTree metadata, allowing
only the generated candidate and changes inside this Workflow Manager checkout.
That historical exception allows the promotion implementation and local run
artifacts to evolve after generation; it does not exempt Horus Work or other
Work repositories. The existing narrowly scoped volatile Obsidian exclusions
still apply. A second baseline covers the entire promotion invocation, including
Workflow Manager itself. Unexpected changes before the canonical write block it;
changes detected afterward make the operation fail. No arbitrary allowlist can
be supplied in source text or CLI arguments.

The canonical content uses only the literal metadata substitutions declared in
`PROMOTION_REPLACEMENTS` in `wm.py`: the title and status become canonical,
promotion statements become approval provenance or future-change rules, and
specific references to this candidate become references to this profile. All
other content is preserved. Every substitution and count is recorded in both
reports. Required policy wording remains intact, with the candidate-specific
promotion rule transformed to retain explicit human approval for future changes.
Unrecognized candidate-only language blocks promotion for a separately reviewed
transformer update; the tool does not attempt a semantic rewrite.

The only canonical file created is `Mind/Vault/Agents/Horus/Profile.md`. Its
missing `Agents`/`Horus` parent directories may be created as necessary. Directory
handles and no-follow flags prevent symlink traversal during creation; exclusive
creation prevents overwriting. The original candidate, proposal, and all prior
run artifacts remain as audit evidence. There is no move, cleanup, deletion,
index mutation, commit, push, account access, or Horus Work write.

The profile is read back and compared with the expected transformation. Required
policies, candidate/input preservation, and Mind working-tree/staged
`git diff --check` are verified. Untracked output receives its own whitespace
validation. As with generation, a fixture without a Mind Git repository reports
the Git check as not applicable. Scope is checked again through report creation.

Each attempt writes JSON and human-readable Markdown reports to unique files in
`.promotions/`, including failed attempts and replays. Reports contain source run
bindings, hashes, explicit approval, transformations, checks, observed changes,
limitations, and next actions. Prior reports are never overwritten. These local
artifacts are Git-ignored and use mode 0600. Exit 0 means `promoted`; exit 1 means
failure. If reports cannot be written, the command fails and reports that on
stdout. A filesystem failure may leave partial output or reports: inspect them
manually. A post-write failure never triggers deletion or rollback, and an
existing canonical output blocks subsequent attempts.

The source run is trusted local, unsigned evidence, and `--approve` is an operator
assertion rather than an authenticated identity or signature. Deterministic
validation cannot repeat semantic review. Run on a quiet trusted filesystem:
metadata snapshots are not continuous monitoring or protection against hostile
same-user changes. Report finalization also has a finite observation window.

## Verification

```sh
python3 -m unittest discover -s tests -v
python3 -m py_compile wm.py tests/test_wm.py tests/test_promotion.py
git diff --check
```

There are no configured lint/typecheck dependencies or build step. Tests cover
required policies, source structure, prompt binding, secret rejection, missing
inputs, symlinks/hardlinks, overwrite/replay prevention, failed Git checks,
unexpected Work/Mind/Git/run writes, and a successful staging/report round trip.

Promotion tests also cover approval, source eligibility, hash and policy failures,
missing/non-regular/symlink candidates, destination collisions, scope changes,
readback and Git failures, evidence retention, report creation, and exclusive writes.
