# claude-ntfy-hook

Approve or deny Claude Code permission prompts from your phone.

When Claude Code is about to ask whether it can run a tool (Bash, Write, etc.), this hook publishes a push notification to your phone via [ntfy](https://ntfy.sh) with **Allow** / **Deny** action buttons. Tap one and Claude Code skips the local prompt. Don't tap and the local prompt fires as usual — the hook never silently blocks a tool call.

```
Claude Code  ──PermissionRequest──▶  ntfy_permission.py
                                          │
                                          ├─▶ POST ntfy.sh/<perm-topic>     (publish, w/ Allow/Deny actions)
                                          │
                                          ├─◀ poll ntfy.sh/<resp-topic>     (subscribe, await user tap)
                                          │
                                      decision JSON
                                          │
                                          ▼
                                  Claude Code (allows / denies)
```

## Why a hook and not Remote Control

Claude Code's [Remote Control](https://code.claude.com/docs/en/remote-control) mirrors a session to web/mobile but doesn't let you respond to permission prompts remotely — those are terminal-only. This hook fills that gap by intercepting the `PermissionRequest` event and routing the decision through ntfy.

## Setup

### 1. Install ntfy on your phone

- Android: [F-Droid](https://f-droid.org/packages/io.heckel.ntfy/) or Google Play.
- iOS: [App Store](https://apps.apple.com/app/ntfy/id1625396347).

### 2. Generate two topic names

Topics on public ntfy.sh are unauthenticated — anyone who guesses the topic name can publish to it. Use long random strings as a shared secret:

```sh
echo "NTFY_PERM_TOPIC=claude-perm-$(openssl rand -hex 12)"
echo "NTFY_RESP_TOPIC=claude-resp-$(openssl rand -hex 12)"
```

### 3. Subscribe to the perm topic on your phone

Open the ntfy app → Subscribe → topic name = your `NTFY_PERM_TOPIC` value, server `https://ntfy.sh`. Smoke test:

```sh
curl -d "if you see this, ntfy is working" https://ntfy.sh/$NTFY_PERM_TOPIC
```

### 4. Install the hook script

Symlink (or copy) the script into your Claude Code scripts dir:

```sh
mkdir -p ~/.claude/scripts
ln -sf "$PWD/ntfy_permission.py" ~/.claude/scripts/ntfy_permission.py
chmod +x ~/.claude/scripts/ntfy_permission.py
```

### 5. Wire the hook into `~/.claude/settings.json`

Merge this into your existing `hooks` block (don't replace anything else there):

```json
{
  "hooks": {
    "PermissionRequest": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "python3 ~/.claude/scripts/ntfy_permission.py",
            "timeout": 60
          }
        ]
      }
    ]
  }
}
```

We use `PermissionRequest` rather than `PreToolUse` so the hook only fires when Claude Code was actually going to prompt — not for every tool call. Tools that are already auto-allowed by your `permissions.allow` patterns or by the safe-command classifier won't notify your phone.

The hook's internal wait (`NTFY_TIMEOUT`, default 30 s) should be lower than the Claude Code `timeout` field (set above to 60 s) so the harness never kills the hook mid-poll.

### 6. Persist env vars

Add to `~/.zshrc` (or `~/.bashrc`):

```sh
export NTFY_PERM_TOPIC=claude-perm-<your-random-12-bytes>
export NTFY_RESP_TOPIC=claude-resp-<your-random-12-bytes>
```

Open a new terminal so Claude Code inherits them, then run `claude` and ask it to do something that requires permission (e.g. `write "hi" to /tmp/foo`). Your phone should buzz.

## Configuration

All env vars read by the hook script:

| Var | Default | Purpose |
|---|---|---|
| `NTFY_PERM_TOPIC` | required | Outbound topic. The phone subscribes here. |
| `NTFY_RESP_TOPIC` | required | Inbound topic. Action buttons publish here. |
| `NTFY_BASE` | `https://ntfy.sh` | Override to use a self-hosted ntfy server. |
| `NTFY_TIMEOUT` | `30` | Seconds to wait for a tap before falling through to the local prompt. |
| `NTFY_AUTH` | unset | Optional `Bearer tk_...` or `Basic <base64>` for self-hosted ntfy with auth. |

If `NTFY_PERM_TOPIC` or `NTFY_RESP_TOPIC` is not set, the script exits 0 with no output — the hook becomes a no-op and Claude Code uses the normal terminal prompt.

## Privacy

The hook sends the tool name, the tool's first input field (the Bash command, the Edit/Write file path, etc.), and the basename of `cwd` to ntfy.sh. On public ntfy.sh, this transits Heckel's servers in plaintext. If that's a problem:

- Self-host ntfy ([single-binary, Docker one-liner](https://docs.ntfy.sh/install/)) and point `NTFY_BASE` at it.
- Or trim what `short_summary()` includes in `ntfy_permission.py` before publishing.

## Behavior reference

| Event | Hook response | Claude Code result |
|---|---|---|
| You tap **Allow** | `decision.behavior: "allow"` | Tool runs without local prompt. |
| You tap **Deny** | `decision.behavior: "deny"` | Tool is denied with the hook reason. |
| You don't tap (timeout) | no JSON output | Normal local terminal prompt fires. |
| ntfy.sh unreachable | exit 0, no output | Normal local terminal prompt fires. |
| Env vars missing | exit 0, no output | Normal local terminal prompt fires. |

The hook is purely additive: worst case it's a no-op.

## License

MIT. See `LICENSE`.
