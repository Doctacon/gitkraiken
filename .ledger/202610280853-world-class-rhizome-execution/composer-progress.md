# M1 composer continuation

## Authority and scope

The operator resumed roadmap execution with recommendations while explicitly
continuing to defer crash/retry. The next bounded increment is configured commit
templates and native Git message semantics, not another prerequisite for ordinary
commits. Preserve the working coordinator/index transaction and terminal handoff.
No installer changes, process-supervisor redesign, automatic retry, publication,
installation, or project commit.

The operator selected **Existing checks only** when offered public Repository and
Pilot test additions. No new or changed tests, fixtures, or smoke scripts belong
to this increment. Existing regressions can detect regressions in covered behavior;
they do not establish new template/encoding/cleanup-matrix acceptance.

## Implemented increment

- Restore any nonempty saved draft before considering templates. Comments and
  whitespace are still user text; do not read a template over them.
- Resolve configured `commit.template` using Git's effective path configuration;
  read only a bounded regular file and decode using `i18n.commitEncoding`.
- Seed an empty composer on repository open, without persisting an untouched
  template. Require an edit before committing the exact seed. Report load errors;
  let the user write their own message without replacing an existing draft.
- Keep the composer read-only during restoration and reject line endings that
  Textual would silently normalize. Preserve stored originals on failure.
- Submit accepted text through anonymous-file stdin to native
  `commit --edit --no-status --no-verbose -F -`. Git owns cleanup/comment rules
  and hooks; no second editor or invisible status/diff text is inserted.
- Strict encoding failures occur before opening the index transaction. Captured
  encoding is pinned for the native invocation. Root additionally restricted the
  encoding label to an ASCII name: Python accepts some punctuation/whitespace
  aliases that must not become literal commit-header delimiters. Message text is
  not placed in argv or retained transaction context.
- Keep ordinary commits enabled. Existing terminal/signing, candidate-index,
  process-group, success identity, and uncertain-outcome protections are unchanged.

The older `composer-implementation-plan.md` is technical background only; its
commit-gating instructions are superseded by restoration and current scope.

## Validation

All runs used existing tests/scripts only, on macOS ARM64 with Python 3.13.0,
current Git 2.50.1 (Apple Git-155), actual upstream Git 2.36.0 built previously in
`/tmp/rhizome-git236.zSmABEnN/git-6cd33dceed60949e2dbc32e3f0f5e67c4c882e1e`,
and tmux 3.5a. No system install or user repository was used for mutations.

| Existing check | Result | Evidence |
| --- | --- | --- |
| Current Git: app, workspace state, precision, responsive, operation workspace, diff workspace, refresh workspace | **117 passed in 518.62 s** | `composer-ui-existing.txt` |
| Current Git: repository engine, coordinator, patches, persistence, read-only index | **131 passed in 119.42 s** | `composer-core-existing.txt` |
| Actual Git 2.36: commit transactions/representations/processes/faults/publication/PTY, coordinator, precision, workspace state | **90 passed in 86.93 s** | `composer-git236-existing.txt` |
| Current Git: builder's existing native and commit-file batches | **10 + 3 + 31 passed** | `composer-engine-result.md` |
| Existing `scripts/commit_smoke.py`, both current and actual Git 2.36 | **Passed on both** | `composer-terminal-existing.txt` |
| Final native transactions + three real PTY cases, after root's encoding-name validation | **13 passed in 15.40 s** current; **13 passed in 8.12 s** Git 2.36 | `composer-final-native-existing.txt` |
| Full source/tests + existing smoke Ruff; `git diff --check` | **Passed** | root tool output |

The terminal smoke retains exact multiline/trailing-whitespace input under
configured `verbatim`, exercises a native interactive hook refusal with preserved
draft, then commits only the selected change and resumes the real 80×24 UI. PTY
cases include `/dev/tty`, stderr prompts, and real disposable-key SSH signing.
These are existing automated checks, not new template coverage or human pilots.
The broad batches predate only the final encoding-name syntax check; the final
native batch reran afterward. Counts overlap and must not be summed as unique tests.

No full suite was rerun in this increment. The prior full suite still has the
known installer crash/retry failure; it was neither skipped, changed, nor
reclassified. No milestone/release acceptance is implied.

## Remaining composer gaps

Dedicated template-path/error/newline/encoding and native cleanup-oracle coverage
was not added, per operator choice. Template seeding occurs on repository open;
after successful commit, the composer remains cleared until a later reopen or
user edit. Broader assistive guidance remains future work; existing summary/body
label and staged-file context remain available without a mandatory style policy.

## Recommended continuation

Stay in M1: expose the existing local diagnostics/retention controls and close
remaining core persistence/lifecycle gaps, then gather compatibility/performance
and usability evidence. Do not jump to M2 or reopen installer crash/retry merely
because the covered regression slices are green. Dedicated new-feature tests
remain an explicitly unfilled validation item rather than an implied pass.
