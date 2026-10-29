## Judgment

Use the constrained native-summary binding. It is the smallest credible route that preserves native hooks and signing. There is no preferable documented machine-readable commit-success channel in Git 2.36.

### Source grounding

The saved Git 2.36 source establishes the critical identity chain:

- `commit_tree_extended(..., &oid, ...)` stores the created commit OID at `composer-sources/commit.c:1818-1820`.
- `update_head_with_reflog(..., &oid, ...)` updates the ref to that same OID at `commit.c:1827-1831`.
- `post-commit` runs afterward at `commit.c:1848-1849`.
- The unchanged local `&oid` is then passed to `print_commit_summary()` at `commit.c:1853-1861`.

Therefore a post-commit ref substitution cannot alter the OID supplied to the summary printer.

One correction: Git 2.36’s `print_commit_summary()` implementation is outside this saved file, in upstream `sequencer.c`. It uses `%h`, not `%H`, for the summary. `core.abbrev=no` makes `%h` full-length, as documented in `core-config.txt:707-714`. Pinning the matching `sequencer.c` is still needed to source these exact emission facts:

1. It looks up and prints the commit identified by its `oid` argument, rather than resolving the commit from `HEAD`.
2. It constructs the header using `format:%h] %s`.
3. Any post-hook `HEAD` resolution is used only for the displayed branch/detached label.
4. The summary goes to stdout and its color controls can be deterministically disabled.

### Recommended bounded contract

For this private invocation only, run native commit with:

- the existing `LC_ALL=C`;
- `core.abbrev=no`;
- the source/oracle-proven color overrides;
- no `--quiet`.

Parse raw stdout, not decoded/redacted friendly output. Extract the full OID only from the native-shaped summary header. Do not parse the subject and do not trust the displayed branch label. Require exactly one recognized candidate; missing or multiple candidates means `GitOutcomeUncertain`.

On return code zero, success should require all of:

- the parsed OID resolves as a commit;
- its complete parent list is exactly the captured old HEAD, or empty for unborn HEAD;
- final HEAD equals that parsed OID;
- final symbolic HEAD equals the captured symbolic target;
- index publication and durability succeeded.

The parent check must move from `after[0]` to the parsed OID. The current comparison at `src/rhizome/git.py:851-861` only proves that final HEAD is *a* suitable sibling; it does not prove it is Git’s created commit. Candidate publication should remain independent and occur before outcome classification, as it does at `git.py:846-850`.

This makes the new regression fail closed: the summary identifies Git’s actual commit, while final HEAD identifies the substituted sibling, so equality fails.

## Why not another source?

- `git commit --porcelain` is not a committing interface. In Git 2.36, selecting a status format sets `dry_run` at `commit.c:1358-1359`; the saved manual also describes porcelain only for dry-run at `git-commit.txt:151-154`.
- Reflogs are optional.
- Trace/Trace2 output is not a documented created-commit-OID contract.
- Object-database scans cannot reliably distinguish the created commit amid concurrent object creation.
- A `reference-transaction` hook wrapper could capture it, but preserving and sequencing existing hooks is materially larger and more invasive.

## Limits and required proofs

This remains a version-sensitive compatibility seam, not porcelain. Before ungating, prove on Git 2.36 and current Git:

- ordinary, detached, and unborn summaries produce exactly one full SHA-1 OID; SHA-256 too if supported;
- forced user color settings are overridden or cause uncertainty;
- post-commit same-parent substitution yields parsed-created-OID versus final-HEAD mismatch;
- hook output containing a fake summary, including output without a trailing newline, produces ambiguity rather than success;
- summary-shaped subjects or paths cannot select the wrong OID;
- no configuration can suppress the summary without `-q`—`quiet` is an option at `commit.c:1620`, and `git_commit_config()` does not configure it.

Because hooks share stdout, the summary is not an authenticated channel against a hostile hook that tampers with the parent process or its descriptors. Ordinary hook lookalikes must merely force uncertainty. That limitation is consistent with the existing exclusion of arbitrary same-UID filesystem actors.