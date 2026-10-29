## Judgment

Keep fetch gated.

The new `os.walk(..., onerror=...)` handling and Git-2.36-compatible `for-each-ref` fix close outbound-inventory gaps, but not this one:

- `src/rhizome/git.py:1915-1970` inventories symbolic refs only under `refs/remotes`.
- `git.py:1972-2010` walks outward from configured fetch destinations.
- It never examines inbound `refs/heads/*` or `refs/tags/*` aliases.

Therefore a resolving alias such as:

```text
refs/heads/alias -> refs/remotes/origin/main
```

will report OID A before a force-capable cache update and OID B afterward. Its stored symbolic target is unchanged, but its observable local-branch tip changed. With `core.logAllRefUpdates=false`, neither the alias nor tracking ref has guaranteed recovery; an unrelated A can become unreachable after `FETCH_HEAD` is replaced.

This is not equivalent to a dangling alias changing from no OID to B. That creates a resolution but loses no pre-existing OID.

## Smallest coherent policy

Before a selected fetch:

1. Boundedly inspect symbolic refs under only `refs/heads` and `refs/tags`.
2. Follow each simple symbolic chain.
3. If it currently resolves to a nonzero OID and reaches a remote-cache ref covered by a selected positive destination, reject the fetch. This also covers pruning that could turn it nonzero→missing.
4. Permit currently dangling inbound aliases; zero→OID is creation, not loss.
5. Keep native non-force tag auto-follow if real-Git tests prove:
   - a resolving symbolic tag cannot change a nonzero target;
   - a dangling symbolic tag only makes an absent target resolvable and preserves the symbolic target text.

No universal ref graph is needed. Rejection plus external Git handoff is smaller than adding recovery to automatic fetch. Arbitrary hooks and same-UID post-validation races remain expressly outside this deterministic-layout check.

## Required disposable proof

The acceptance matrix should use public `Repository.execute(Action("fetch", remote="origin"))`, isolated `HOME`, `XDG_CONFIG_HOME`, and all inherited `GIT_*`, with `GIT_CONFIG_NOSYSTEM=1`, `GIT_ALLOW_PROTOCOL=file`:

| Case | Required oracle |
|---|---|
| Resolving head and tag aliases into tracking ref | A→B for both aliases after unrelated forced remote rewrite; symbolic target unchanged; no alias/tracking reflogs with `core.logAllRefUpdates=false`; A unreachable under `git fsck --unreachable --no-reflogs` |
| Dangling head/tag aliases into tracking ref | unresolved→B; symbolic targets unchanged; classify as creation |
| Dangling symbolic tag named by reachable remote tag | record whether native auto-follow writes through it, preserves symbolic text, and only creates an absent target |
| Resolving symbolic tag targeting an existing local branch | existing branch/tag OID must remain unchanged |

Run the tag cases on Git 2.36 and current Git. The existing test at `tests/test_fetch_safety.py:286-302` proves only an ordinary direct conflicting tag, not symbolic tags.

I could not execute these probes in this structurally read-only consultant environment, so this is static judgment, not the requested real-Git execution evidence. That uncertainty itself is sufficient reason not to ungate yet.