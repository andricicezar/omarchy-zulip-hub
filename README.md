# Zulip Hub for Omarchy

[Version française](README.fr.md)

Zulip Hub brings unread counters, recent conversations, messages read in
place, native notifications, direct-message composition, replies in the
original conversation, and an optional Zulip workspace to the Omarchy bar. The interface follows the current Omarchy theme and is fully usable with
the keyboard.

![Zulip Hub preview](preview.png)

## Requirements

- Omarchy with the Quattro shell plugin system
- Python 3.11 or newer
- `secret-tool` from libsecret
- A personal Zulip API key

The plugin runs unsandboxed, like every Omarchy shell plugin. It connects only
to the HTTPS Zulip server configured by the user. The API key is sent to the
local helper over standard input and stored in Secret Service; it is never
written to the repository, configuration file, shell state, or process
arguments.

## Install

```sh
omarchy plugin add https://github.com/valentin-briez-banuls/omarchy-zulip-hub.git --enable
```

Open the bar widget and enter the Zulip server URL, account email, and API key.
No terminal is needed after the plugin has been added.

## Multiple Zulip servers

Add further accounts from **Settings → Zulip accounts → Add Zulip account**.
The bar shows the combined unread count; the panel merges recent conversations
from every server, each labelled with its account, and replies, reads, and
"mark as read" always go to the conversation's own server. New direct messages
let you pick the account to send from, and notification titles carry the
account name.

The same can be written by hand in `~/.config/zulip-hub/config.toml`:

```toml
[[accounts]]
name = "FStar"            # optional label
site = "https://fstar.zulipchat.com"
email = "me@example.org"

[[accounts]]
site = "https://zulip.example.org"
email = "me@example.org"
```

then store each key with `zulip-hub login --site <url>`. The older single
`[account]` section is still read, and is converted the first time the
account list is changed from the panel. One account without a key, or offline,
does not stop the others; Settings → Diagnostics shows each account's state.

The optional **Omarchy integration** control in Settings adds:

- `Super+Z` — open or close Zulip Hub
- `Super+Shift+Z` — show or hide the dedicated Zulip workspace

The control changes only the user-owned Hyprland configuration under
`~/.config/hypr/` after explicit activation.

## Update

```sh
omarchy plugin update io.github.valentin-briez-banuls.zulip-hub
omarchy-restart-shell
```

The restart is not optional. The bridge picks up a new version straight away,
but Omarchy keeps a bar widget's loaded component when the plugin URL has not
changed, so the interface only changes once the shell restarts.

The panel notices this on its own: when the shell is behind the installed
files it shows a banner with a **Restart now** button, so the second command
is only there for those who prefer the terminal.

## Keyboard controls

- `Tab`, arrows, or `j` / `k` — navigate
- `Enter` — show the selected message in the panel
- `A` — reply in the selected conversation
- `O` — open the selected message in Zulip
- `Ctrl+Enter` — send the message
- `X` — mark the selected message as read
- `R` — refresh
- `Escape` — go back or close

## Data and permissions

Zulip Hub creates user-owned data only:

- `~/.config/zulip-hub/config.toml` — accounts and preferences, mode `0600`
- `~/.local/state/zulip-hub/` — public counters and control markers
- Secret Service — one personal API key per account
- optionally `~/.config/hypr/zulip_hub.lua` plus one managed include block

Message bodies and drafts are not persisted. Reading a message fetches its
body on demand and keeps it in memory only; it is dropped when the panel
closes, and is rendered as plain text so that a remote sender cannot drive
formatting or resource loading. A reply carries only the message identifier to the local helper, which
resolves the destination from its own state, so the interface can never
redirect a message. Notification content is redacted
while the session is locked or when lock state cannot be confirmed.

## Remove

First open **Settings → Omarchy integration** and remove the shortcuts. Then:

```sh
omarchy plugin remove io.github.valentin-briez-banuls.zulip-hub
```

Configuration and the keyring entry are preserved deliberately. To erase the
key, use **Edit Zulip account → Disconnect** before removal.

## Development and validation

```sh
PYTHONPATH=src python -m unittest discover -s tests -v
omarchy plugin validate .
tests/run_release_checks.sh
```

Tests never use real Zulip credentials.

## License

MIT © 2026 Valentin Briez-Banuls
