# Native commit result identity — source evidence and correction

Primary Git **v2.36.0** sources are cached under `composer-sources/`. Each begins
with its exact upstream URL. These are reference material, not Rhizome runtime
code or evidence of executing Git 2.36.

## Result identity

- `builtin/commit.c` (cached as `commit.c`) lines 1818–1831 creates `oid` and updates
  the ref to it. Lines 1848–1861 run `post-commit`, then pass that same local `oid`
  to the summary printer. A hook changing HEAD does not change the passed OID.
- `sequencer.c:1272–1352` looks up the passed OID, formats `format:%h] %s`, and emits
  the `[branch ... OID] subject` summary. HEAD supplies the display label, not the
  created object identity. Commit subjects and indented file summaries are data.
- `core-config.txt:707–715` documents `core.abbrev=no` as full-length object names.
- The private invocation uses this option and disables UI/diff colors, without
  changing repository configuration. Raw stdout is parsed before friendly output
  or redaction. A missing/multiple native-shaped header is uncertain.
- Parent verification reads the actual object with `--no-replace-objects cat-file`,
  not history traversal influenced by replace refs/grafts. Success requires the
  created commit OID to equal final HEAD, unchanged symbolic target, and exactly
  the captured parent (or no parent for an initial commit).

## Important correction to the architectural report

The initial `commit-result-identity-review.md` assumed ordinary hooks shared Git's
stdout and required hook lookalikes to be ambiguous. Actual pinned source says
otherwise:

- `commit-core.c:1726–1747` passes the candidate `GIT_INDEX_FILE` into `run_hooks_opt`.
- `hook.c:46–76` sets `cp->stdout_to_stderr = 1`.
- `hook.c:116–155` waits for hook completion through the parallel runner with one job.

Thus ordinary pre/post-commit stdout (even without a newline) is **stderr** of the
native Git invocation. It cannot be confused with the native stdout header. This
is also exercised with actual current Git, not an internal mock. Arbitrary hooks
that tamper with parent descriptors or daemonize are outside this guarantee.

## Exact local evidence so far

- Same-parent post-commit ref substitution: **1 failed in 1.51 s** → after binding
  the created OID, **2 passed in 2.03 s** with the writer-exclusion test.
- Pre-commit stages another path and succeeds/rejects: **2 passed in 2.35 s**.
  The candidate index is published in both normal outcomes; rejected commits keep
  HEAD and all staged hook edits, without mislabeling the ordinary rejection uncertain.
- A full-OID-looking subject initially caused false uncertainty: **1 failed in
  1.56 s**. Matching only the native header prefix (not its subject) fixed it.
  The test also covers forced colors, user abbreviation=4, a `]` branch name,
  a header-looking filename, and hook lookalikes with/without trailing newlines.
- Current component slice: **5 passed in 5.39 s**, Ruff passed.

This is Git 2.50.1 on the current host. The coordinator and UI remain gated;
Git 2.36 runtime, representations, signing, ref races, interruption, and failure
publication proofs are still outstanding. No milestone or release is accepted.
