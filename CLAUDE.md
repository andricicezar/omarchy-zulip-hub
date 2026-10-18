# Zulip Hub — notes for coding agents

Omarchy shell plugin: a Python bridge (`src/zulip_hub/`) that talks to one or
more Zulip servers, and a QML bar widget (`plugin/`) that reads its state.

## Layout

- `run-plugin.py` — entry point used by the installed plugin; sets
  `ZULIP_HUB_EMBEDDED=1` and calls `zulip_hub.cli.main`.
- `src/zulip_hub/cli.py` — subcommands. `daemon` runs one event queue per
  account (`daemon.MultiBridge`) and merges their state
  (`state.MergedStateStore`) into `~/.local/state/zulip-hub/state.json`.
- Short-lived JSON-over-stdin helpers called by QML: `onboarding` (accounts,
  settings, sign-in), `compose`, `read-message`.
- `browser_login.py` — browser sign-in (Zulip's `mobile_flow_otp` flow via a
  temporary `zulip://` handler), password sign-in, zuliprc import.
- `launchers.py` — one `omarchy-webapp-install` launcher per account.
- `opening.py` — opening messages and focusing/launching an account's web app.
- `plugin/Panel.qml` — the whole panel UI; `Onboarding.qml`, `Composer.qml`,
  `Reader.qml` are its controllers; `ZulipService.qml` reads `state.json`;
  `Model.js` normalizes it; `I18n.js` holds fr/en strings.
- Config: `~/.config/zulip-hub/config.toml` (`[[accounts]]`, `[open]`,
  `[development]`, …); example in `config/config.example.toml`.

## Rules

- **Never run real side effects from tests.** Launchers only act when
  `ZULIP_HUB_EMBEDDED=1`; commands go through `commands.run/spawn` and must be
  mocked. Do not use real organization servers or real emails in tests — use
  `example.org`-style hosts.
- Secrets (API keys, passwords) travel over stdin only, never in process
  arguments, logs, the config file, or replies to the UI.
- Replies/reads carry only a message id plus its `site`; the bridge resolves
  the destination from its own state and refuses unconfigured servers.
- Hyprland 0.56+ evaluates `hyprctl dispatch` arguments as Lua; see
  `HyprlandController.focus_window` for the pattern (Lua first, then classic).
- Match the surrounding style: comments and user-facing messages in the Python
  bridge are in French; QML strings go through `I18n.js` in both languages.
- Bump the version in both `manifest.json` and `pyproject.toml`, and add a
  `CHANGELOG.md` entry (French) for user-visible changes.

## Checks

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest discover -s tests   # unit tests
tests/run_qml_integration.sh                                                     # QML harness
tests/run_release_checks.sh                                                      # everything, incl. omarchy plugin validate
```

`tests/run_release_checks.sh` must end with `ZULIP_HUB_RELEASE_CHECKS_OK`.

## Trying a change on the running desktop

The installed copy lives in `~/.config/omarchy/plugins/io.github.valentin-briez-banuls.zulip-hub`
and is updated from the default branch of its git remote:

```sh
git push origin HEAD:main                      # only when the user asks
omarchy plugin update io.github.valentin-briez-banuls.zulip-hub --yes
omarchy-restart-shell                          # needed for QML changes
omarchy-shell io.github.valentin-briez-banuls.zulip-hub open   # open the panel
```

Python changes take effect on the next helper call; the bridge restarts when
the config or `~/.local/state/zulip-hub/restart` changes.
