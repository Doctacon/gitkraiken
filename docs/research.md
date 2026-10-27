# UI research and implementation choices

## Primary sources

The **current GitHub source**, not the older locally installed apple-pi checkout,
was inspected at commit [`31dc4a18bd4f9cd33f398609f0a1131bf6cc172d`](https://github.com/z3z1ma/apple-pi/tree/31dc4a18bd4f9cd33f398609f0a1131bf6cc172d).

- [Tmux integration documentation](https://github.com/z3z1ma/apple-pi/blob/31dc4a18bd4f9cd33f398609f0a1131bf6cc172d/docs/tmux-sessions.md): the sidebar/dashboard are Python **Textual** apps in ordinary tmux panes/windows. The macOS notch is a separate Swift/web implementation, not the tmux UI.
- [Dashboard implementation](https://github.com/z3z1ma/apple-pi/blob/31dc4a18bd4f9cd33f398609f0a1131bf6cc172d/components/tmux-sessions/sidebar/dashboard.py): `Dashboard.compose`, `Dashboard.CSS`, `on_resize`, `poll`, `refresh_preview`, and `put_cell` show a dense table/detail workspace, responsive layout, worker-based polling, visibility-aware work, and minimal repainting. The actual `NARROW_COLUMNS` constant is 180 at this revision, despite prose saying 140.
- [Sidebar implementation](https://github.com/z3z1ma/apple-pi/blob/31dc4a18bd4f9cd33f398609f0a1131bf6cc172d/components/tmux-sessions/sidebar/sidebar.py): `Card.paint`, `render_agents`, and `sync_lane` demonstrate semantic colors, quiet labels, mouse/keyboard navigation, and stable widgets. The source uses terminal-default transparency and a Catppuccin-inspired palette.
- [Tmux launcher](https://github.com/z3z1ma/apple-pi/blob/31dc4a18bd4f9cd33f398609f0a1131bf6cc172d/components/tmux-sessions/scripts/sidebar.sh) and [plugin](https://github.com/z3z1ma/apple-pi/blob/31dc4a18bd4f9cd33f398609f0a1131bf6cc172d/components/tmux-sessions/pi_session_manager.tmux): long-lived processes, quoted shell arguments, server-environment awareness, and idempotent integration matter. Rhizome uses opt-in windows/popups instead of installing hooks or following windows.
- [GitKraken's main interface guide](https://help.gitkraken.com/gitkraken-desktop/interface/): the main screen is toolbar + refs sidebar + commit DAG + commit/staging panel. WIP selects pending changes. Toolbar actions include pull/push, branch, stash/pop; reference and commit actions belong with the selected item. This layout and workflow, not proprietary assets, informed this original terminal adaptation.

## Recommendations adopted

1. Keep the existing Python project. Use Textual rather than add a browser server,
   Electron shell, or a second state store.
2. Use Git's **native graph output** so lane and merge topology are real, including
   octopus merges. Search moves to matching rows; it does not remove ancestors and
   fabricate graph connections.
3. Keep blocking Git work off the event loop. Serialize repository operations;
   never cancel an asynchronous wrapper while its subprocess is still mutating.
4. No application accounts or token vault. Git credential helpers and SSH agents
   already support private GitHub and Azure DevOps repositories.
5. Read local state periodically; network actions are explicit. Confirm destructive
   actions. No force push, hard reset, clean, or silent worktree disposal.
6. Use a distinct dark navy/teal terminal palette and original naming/assets. This
   is not an official GitKraken product and does not claim feature-for-feature or
   pixel-for-pixel parity with the proprietary desktop application.
