# Canonical patch foundation (C2 / V01)

## Integrator API

```python
from rhizome.patches import PatchDocument, PatchLine, PatchService

service = PatchService(repo)  # repo is rhizome.git.Repository
view = service.open("path/from/repository/root", staged=False, context=3)
message = service.apply(view, start_line_id, end_line_id)
```

- `PatchService.open(path: str, *, staged: bool = False, context: int = 3) -> PatchDocument`
  captures either index→worktree (`staged=False`) or HEAD/empty-tree→index
  (`staged=True`). `context` is an integer from 0 through 10,000.
- `PatchService.apply(document, start: int, end: int) -> str` treats `start` and
  `end` as an inclusive contiguous range of document-local displayed IDs. The range
  must contain at least one `add` or `delete`. It stages when `document.staged` is
  false and unstages when true. It changes only the index, never the working file.
- `PatchDocument` is frozen and exposes `path`, `staged`, `lines` (a tuple),
  `selectable`, and `reason`. Exact patch bytes, byte payloads, endpoint data, and
  fingerprints are retained in private immutable fields; the UI must pass the
  original document back rather than reconstructing it from rendered text.
- `PatchLine` is frozen and exposes `id`, `kind` (`context`, `add`, `delete`, `hunk`,
  or `meta`), `text`, `hunk`, `old_line`, and `new_line`. IDs and hunk numbers are
  1-based. `text` is the canonical unified-diff display line, including its prefix
  and excluding the patch record LF; undecodable bytes use Python surrogate escape.
- Selecting all displayed lines belonging to a hunk applies that hunk. A range may
  cross hunk headers/context and multiple hunks. Selecting only one side of a
  replacement has literal patch-editor semantics: an add-only selection retains the
  old line and inserts the new line; a delete-only selection removes the old line.
- Success returns `"Staged selected lines in PATH."` or
  `"Unstaged selected lines in PATH."`. Invalid, stale, unsupported, locked-index,
  and Git failures raise `rhizome.git.GitError`.

A non-selectable document is the safe handoff signal. `reason` says why line staging
is unavailable and directs the caller to an existing whole-file stage/unstage flow;
`apply` refuses such a document.

## Canonical behavior

The service parses Git's exact unified patch once and preserves byte payloads
separately from display strings. Staging reconstructs the index result by applying
only selected index→worktree changes to the captured index blob. Unstaging does **not**
naively reverse a selected HEAD→index fragment: it reconstructs from HEAD while
retaining every unselected staged change. This is what preserves adjacent/unrelated
staged additions.

Before mutation and again immediately before `update-index`, the service compares:
repository root, HEAD OID (including unborn HEAD), the complete stage-0 index listing,
worktree path kind/mode/content, endpoint modes/OIDs, exact raw patch, and parsed line
mapping. A changed input is rejected and never remapped. Git object creation and index
mutation both go through `Repository._git`; patch reads go through
`Repository._git_diff`. Every path remains a literal argv value. No production shell
or independent subprocess is used.

## Supported and exercised cases

Real disposable repositories and exact independent index-blob/worktree-byte oracles
cover:

| Case | Result |
| --- | --- |
| Selected replacement with a separate unstaged hunk | Pass; only selected replacement entered index |
| Add-only and delete-only replacement subsets | Pass; literal line semantics verified |
| Whole hunk and a contiguous range spanning two hunks | Pass |
| Prior staged content plus a later selected unstaged edit | Pass; prior index bytes preserved |
| Inverse unstage with adjacent staged insertion and separate unstaged edit | Pass; literal expected index blob preserved insertion |
| Untracked new text in an unborn repository | Pass; partial stage and inverse full unstage |
| Full tracked deletion | Pass; index entry removed |
| CRLF and missing newline at EOF | Pass; exact bytes preserved |
| Spaces, leading `-`, embedded newline, and Unicode in a path | Pass; literal argv/path bytes |
| Changed worktree path, unrelated index entry, or HEAD after open | Pass; refused without target-index mutation |
| Repeated application and ranges with no changes | Pass; refused |
| Existing index lock | Pass; GitError with index/worktree bytes unchanged |
| Binary, executable-bit mode change, symlink, staged rename, custom filter, ignored file | Pass; truthful non-selectable document |
| Patch output over 2 MiB | Pass; truthful non-selectable document |

Regular textual files with modes `100644` and `100755` are supported when their mode
does not change. Added/deleted files and partial results use endpoint existence and
mode explicitly. The endpoint blob limit is 16 MiB; the patch limit is 2 MiB.

## Actual red/green evidence

Initial real-Git test before implementation:

```text
$ uv run --locked pytest tests/test_patches.py -q
E   ModuleNotFoundError: No module named 'rhizome.patches'
1 error in 0.07s
```

After the first implementation, the focused test exposed the intended distinction
between selecting an add line and selecting both sides of a replacement:

```text
$ uv run --locked pytest tests/test_patches.py -q
E assert 'one\ntwo\nTWO\nthree\nfour\n' == 'one\nTWO\nthree\nfour\n'
1 failed in 0.70s
```

The behavioral test was corrected to select the contiguous delete+add replacement;
the next run was green (`1 passed in 0.68s`). The final owned-file checks were:

```text
$ uv run --locked pytest tests/test_patches.py -q
.....................                                                    [100%]
21 passed in 11.97s

$ uv run --locked ruff check src/rhizome/patches.py tests/test_patches.py
All checks passed!
```

## Refused cases and remaining limits

- Binary patches, symlinks/non-regular entries (including submodules), renames/copies,
  regular-file mode changes, custom diff/filter/working-tree-encoding attributes,
  ignored/repository-internal paths, empty-file changes with no textual lines,
  oversized patches, and oversized endpoint
  blobs are intentionally non-selectable. Use a reviewed whole-file action.
- Reading transformations are not accepted by this API. A future whitespace-hidden,
  word-highlighted, split, or expanded-context UI must retain the original document
  and IDs; it must not synthesize a patch from rendered text.
- There is no standard Git marker for “generated” text. Ordinary generated text under
  the limits is not inferred from content; integrations that classify a file as
  generated should choose the whole-file handoff before offering line selection.
- External-process concurrency is **not fully serialized**. The service rechecks all
  identities immediately before `git update-index`, and Git takes its normal index
  lock, but the existing `Repository` adapter offers no compare-and-swap operation
  spanning that check and mutation. An external writer landing in that final window
  is therefore not proven to be rejected. This implementation does not claim that
  race is solved; stronger proof needs a Repository-level transactional/CAS seam.
- `hash-object -w` may leave an unreachable blob if the subsequent index lock/update
  fails. It does not change the worktree or index, but no cleanup claim is made.
- Focused tests establish the listed foundation behavior on the recorded local Git
  environment. They are not blanket V01 release proof, cross-platform proof, or a
  claim about every attributes/sparse-index configuration.
