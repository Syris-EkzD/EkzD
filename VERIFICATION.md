# WM-V0-01 verification — 2026-09-06

Implementation is complete. The real run `horus-20260906` generated the candidate,
but its final scope audit **failed closed**. Do not treat this run as approved.

## Evidence

- 26 Python unittest tests passed, including success, invalid inputs/policies,
  forbidden writes, overwrite prevention, and a concurrent late-audit failure.
- Python compilation passed. No lint/typecheck/build tools are configured.
- Workflow Manager and Mind `git diff --check` passed; Mind's index check passed.
- New implementation files and the untracked candidate were also checked with
  `git diff --no-index --check` against `/dev/null`.
- All 17 response checks passed: 14 typed boundary declarations plus section
  structure, source references, and prompt binding. The rendered candidate was
  read back and independently compared with the validated response and report hash.
- All nine input hashes still match the prepared context.
- No `Mind/Vault/Agents/Horus/Profile.md` exists. Canonical agent paths were unchanged.
- No Work changes outside Workflow Manager were observed against the metadata
  baseline. Horus Work's Git status remained clean.
- The workflow's only Mind write was the approved Inbox candidate. The audit also
  observed a concurrent change to `Mind/Vault/.obsidian/workspace.json`; its author
  was not established. That change was not waived, reverted, or edited by this task.
- No accounts, secrets, system configuration, commits, pushes, or Git history edits
  were used. Unrelated file contents were not read for the scope audit.

## Artifacts

- Candidate: `~/WhiteTree/Mind/Vault/Inbox/Horus-Agent-Profile-Candidate.md`
- Human report: `.runs/horus-20260906/report.md`
- Machine report: `.runs/horus-20260906/report.json`
- Prepared context and response: `.runs/horus-20260906/prompt.json` and `response.json`

The original late failure exposed a reporting bug: earlier scope-pass text and the
success next action survived the failed result. The implementation and regression
test now cover this. The local historical reports include an explicit correction
note; the original failed result, observed changes, and hashes are preserved.

## Remaining boundary

Investigate the concurrent workspace change before treating this run as verified.
The existing candidate is never automatically overwritten or deleted. Human review
must assess its source support, academic intake gaps, retention/privacy choices,
and future integration authority. Promotion requires separate explicit approval
and a separate step. The handoff uses the current reasoning session; unattended
provider invocation is not configured. Metadata snapshots are an audit, not an OS
sandbox or continuous filesystem monitor.

Final follow-up observation: the metadata audit also observed these Mind paths
after the failed run (ongoing changes were not waived):

- `Mind/Vault/.obsidian/workspace.json`
- `Mind/Vault/Inbox/Horus-Agent-Profile-Candidate.md`
