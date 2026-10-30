# M2 Textual 1.0.0: themes, keymaps, appearance

Researched 2026-10-29 against the installed **Textual 1.0.0**, matching `uv.lock`.
This is implementation evidence, not a terminal-compatibility or accessibility claim.

## Verified APIs

- `Theme` has 11 base colors and `variables: dict[str, str]` (`textual/theme.py:12–37`).
  `App.register_theme()` registers/replaces a named theme; `App.theme` activates it
  (`textual/app.py:1234–1241`, `1260–1275`). Theme changes invalidate and refresh CSS.
  `get_theme_variable_defaults()` supplies app defaults; selected theme variables
  override those defaults (`app.py:1179–1216`).
- `App.set_keymap()` replaces the complete binding-ID→key map; `update_keymap()`
  merges it (`app.py:3654–3677`). Binding IDs should be globally unique but Textual
  does not enforce uniqueness (`binding.py:79–83`). Keymap application detects
  collisions and exposes them to `App.handle_bindings_clash()` via the screen
  (`binding.py:256–319`, `382–386`; `screen.py:361–366`). Rhizome must validate first;
  it must not silently accept a collision and rely on replacement order.
- Focused `Input` has editing bindings including arrows, word arrows, home/end,
  deletion, Ctrl+A/E/D/W/U/F/K/X/C/V and Enter (`widgets/_input.py:63–108`). Textual
  has no application-wide reserved-key registry. Priority global bindings must
  not be remapped onto ordinary typing/editing keys.

Primary source: <https://github.com/Textualize/textual/tree/v1.0.0/src/textual>.
Version-pinned theme guide:
<https://github.com/Textualize/textual/blob/v1.0.0/docs/guide/design.md>, copied to
`m2-sources/textual-1-themes.md`.

## Appearance-query limitation

xterm documents OSC 11 as the VT100 text background color; `?` requests a reply
(`m2-sources/xterm-ctlseqs.txt:2077–2085`, `2112–2117`).
Primary source: <https://invisible-island.net/xterm/ctlseqs/ctlseqs.txt>.
The `www` hostname failed certificate-name validation during download; the direct
hostname succeeded with normal certificate verification. No TLS bypass was used.

**Do not issue OSC 11 in the current Textual runtime.** Its `XTermParser` handles
keys, mouse, focus, bracketed paste, cursor reports and selected mode reports, but
not an OSC background-color reply (`_xterm_parser.py:111–270`). Unknown sequences
can fall through to `reissue_sequence_as_keys()` (`:132–145`, `198–265`), turning a
terminal reply into input. The driver has no appearance-query API
(`drivers/linux_driver.py:394–428`).

Safe current posture: explicit light/dark/high-contrast overrides win. Do not write
an in-band query. A new parser/driver-based detector is a separate compatibility
decision, not an assumed capability. OS appearance detection remains unverified.

## Bounded environment hint (subsequent primary-source check)

The packaged urxvt manual documents `COLORFGBG` as `fg;bg` or `fg;xpm;bg`, allowing
`default` instead of a color index, and defines canonical color indices 0=black,
7=white (8–15 are high-intensity counterparts). Primary manual:
<https://manpages.debian.org/testing/rxvt-unicode/x-terminal-emulator.1.en.html>,
sections ENVIRONMENT and RESOURCES; root retrieved the relevant passages on
2026-10-29. This supersedes the earlier researcher's unverified-COLORFGBG finding.

Implementation posture: recognize only unambiguous canonical background-index
hints (0 dark; 7/15 light); unknown/malformed/default values use the approved light
fallback. This is explicitly a **hint**, not a live terminal probe. Environment
values may be stale and palettes may be customized; explicit overrides win. No
blocking I/O or input consumption is necessary. Actual terminal/SSH/tmux appearance
and screen-reader behavior remain unverified.
