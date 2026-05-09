#!/usr/bin/env python3
"""
Claude Code PermissionRequest hook: route permission decisions through an
ntfy push notification with Allow / Deny / Always action buttons.

Wire as a `PermissionRequest` hook (preferred) or `PreToolUse` hook in
~/.claude/settings.json. The script auto-detects the event from stdin and
emits the matching response shape.

Behavior:
- Publishes a notification to NTFY_PERM_TOPIC describing the tool call.
- Waits up to NTFY_TIMEOUT seconds for a response on NTFY_RESP_TOPIC.
- Allow tap  -> {"behavior": "allow"} -> tool runs.
- Deny tap   -> {"behavior": "deny"}  -> tool rejected.
- Always tap -> {"behavior": "allow"} AND appends a conservative pattern
                to ~/.claude/settings.json permissions.allow so future
                similar calls auto-approve. Logged to
                ~/.claude/.ntfy_permission.log for audit.
- No tap / network error / missing env -> exit 0 with no output. Claude
  Code falls back to the normal terminal prompt — the hook never silently
  blocks.

Required env:
  NTFY_PERM_TOPIC       outbound topic (you subscribe on Android)
  NTFY_RESP_TOPIC       inbound topic (action buttons publish here)

Optional env:
  NTFY_BASE             default https://ntfy.sh
  NTFY_TIMEOUT          seconds to wait, default 30 (settings.json hook
                        timeout should be slightly higher)
  NTFY_AUTH             optional 'Bearer tk_...' or 'Basic <base64>' header
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid


# --------------------------------------------------------------------- #
# Risk classification                                                   #
# --------------------------------------------------------------------- #

URGENT_BASH_PATTERNS = [
    re.compile(p)
    for p in [
        r"\brm\s+(-[rRf]+|--recursive|--force)\b",
        r"\bgit\s+push[^\n]*--force\b",
        r"\bgit\s+push[^\n]*-f\b",
        r"\bgit\s+reset\s+--hard\b",
        r"\bgit\s+clean\s+-[a-z]*f",
        r"\bgit\s+branch\s+-D\b",
        r"\bdrop\s+table\b",
        r"\bdrop\s+database\b",
        r"\btruncate\s+table\b",
        r"\|\s*(sh|bash|zsh)\b",
        r"\bcurl\s[^|]*\|\s*(sh|bash)\b",
        r"\bwget\s[^|]*\|\s*(sh|bash)\b",
        r"\bsudo\b",
        r"\bdd\s+if=",
        r"\bchmod\s+\+s\b",
        r"\bmkfs\b",
        r":\(\)\{",  # fork bomb head
    ]
]

URGENT_TAG = "rotating_light"   # 🚨
HIGH_TAG = "lock"               # 🔒
INFO_TAG = "information_source" # ℹ️


def risk_level(tool_name, tool_input):
    """Return (priority, tag) tuple."""
    if tool_name == "Bash":
        cmd = tool_input.get("command", "") or ""
        for pat in URGENT_BASH_PATTERNS:
            if pat.search(cmd):
                return "urgent", URGENT_TAG
        return "high", HIGH_TAG
    if tool_name in ("Write", "Edit", "NotebookEdit", "MultiEdit"):
        return "high", HIGH_TAG
    return "default", INFO_TAG


# --------------------------------------------------------------------- #
# Body summary                                                          #
# --------------------------------------------------------------------- #


def truncate(s, lim):
    s = s if isinstance(s, str) else json.dumps(s, separators=(",", ":"))
    return s if len(s) <= lim else s[: lim - 1] + "…"


def short_summary(tool_name, tool_input):
    if tool_name == "Bash":
        cmd = tool_input.get("command", "") or ""
        return truncate(cmd, 600)

    if tool_name == "Write":
        path = tool_input.get("file_path", "") or ""
        content = tool_input.get("content", "") or ""
        if content:
            preview_lines = content.splitlines()[:3]
            preview = "\n".join(preview_lines)
            return truncate(f"{path}\n\n{preview}", 600)
        return path

    if tool_name == "Edit":
        path = tool_input.get("file_path", "") or ""
        old_s = (tool_input.get("old_string", "") or "").strip()
        new_s = (tool_input.get("new_string", "") or "").strip()
        old_first = old_s.splitlines()[0] if old_s else ""
        new_first = new_s.splitlines()[0] if new_s else ""
        if old_first or new_first:
            return truncate(
                f"{path}\n- {truncate(old_first, 200)}\n+ {truncate(new_first, 200)}",
                550,
            )
        return path

    if tool_name == "MultiEdit":
        path = tool_input.get("file_path", "") or ""
        edits = tool_input.get("edits") or []
        n = len(edits)
        return f"{path}\n\n{n} edit{'s' if n != 1 else ''}"

    if tool_name == "NotebookEdit":
        path = tool_input.get("notebook_path", "") or ""
        cell = tool_input.get("cell_id", "") or ""
        return f"{path}{f' (cell {cell})' if cell else ''}"

    if tool_name in ("WebFetch", "WebSearch"):
        url = tool_input.get("url") or tool_input.get("query") or ""
        return truncate(url, 400)

    if tool_name == "Read":
        return tool_input.get("file_path", "") or ""

    parts = [f"{k}={truncate(v, 100)}" for k, v in tool_input.items()]
    return truncate(", ".join(parts), 600)


# --------------------------------------------------------------------- #
# "Always" → settings.json mutation                                     #
# --------------------------------------------------------------------- #


def always_pattern(tool_name, tool_input):
    """Build a conservative permissions.allow pattern for the call."""
    if tool_name == "Bash":
        cmd = (tool_input.get("command", "") or "").strip()
        first = cmd.split()[0] if cmd else ""
        return f"Bash({first} *)" if first else None

    if tool_name in ("Write", "Edit", "MultiEdit"):
        path = tool_input.get("file_path", "") or ""
        if not path:
            return None
        d = os.path.dirname(path)
        if d and d != "/":
            return f"{tool_name}({d}/*)"
        return f"{tool_name}({path})"

    if tool_name == "NotebookEdit":
        path = tool_input.get("notebook_path", "") or ""
        return f"NotebookEdit({path})" if path else None

    return f"{tool_name}(*)"


def append_to_allow(pattern):
    """Atomically append `pattern` to ~/.claude/settings.json permissions.allow.
    Returns True on success, False otherwise. Never raises."""
    if not pattern:
        return False
    settings_path = os.path.expanduser("~/.claude/settings.json")
    try:
        with open(settings_path, "r") as f:
            settings = json.load(f)
    except Exception as e:
        sys.stderr.write(f"ntfy_permission: cannot read settings.json: {e}\n")
        return False

    perms = settings.setdefault("permissions", {})
    allow = perms.setdefault("allow", [])
    if pattern in allow:
        return True
    allow.append(pattern)

    tmp = f"{settings_path}.ntfy.{os.getpid()}.tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(settings, f, indent=2)
            f.write("\n")
        os.replace(tmp, settings_path)
    except Exception as e:
        sys.stderr.write(f"ntfy_permission: cannot write settings.json: {e}\n")
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return False

    log = os.path.expanduser("~/.claude/.ntfy_permission.log")
    try:
        with open(log, "a") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\tadded\t{pattern}\n")
    except Exception:
        pass
    return True


# --------------------------------------------------------------------- #
# ntfy I/O                                                              #
# --------------------------------------------------------------------- #


def post(url, data, headers, timeout=5):
    req = urllib.request.Request(
        url,
        data=data.encode("utf-8") if isinstance(data, str) else data,
        headers=headers,
        method="POST",
    )
    return urllib.request.urlopen(req, timeout=timeout).read()


def env_required(name):
    v = os.environ.get(name, "").strip()
    if not v:
        sys.stderr.write(f"ntfy_permission: {name} not set; falling through\n")
        sys.exit(0)
    return v


# --------------------------------------------------------------------- #
# Decision payload                                                      #
# --------------------------------------------------------------------- #


def emit_decision(event_name, behavior, reason):
    if event_name == "PermissionRequest":
        payload = {
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": {"behavior": behavior},
            }
        }
    else:
        payload = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": behavior,
                "permissionDecisionReason": reason,
            }
        }
    print(json.dumps(payload))


# --------------------------------------------------------------------- #
# Main                                                                  #
# --------------------------------------------------------------------- #


def main():
    ntfy_base = os.environ.get("NTFY_BASE", "https://ntfy.sh").rstrip("/")
    perm_topic = env_required("NTFY_PERM_TOPIC")
    resp_topic = env_required("NTFY_RESP_TOPIC")
    timeout = int(os.environ.get("NTFY_TIMEOUT", "30"))
    auth = os.environ.get("NTFY_AUTH", "").strip()

    try:
        event = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    event_name = event.get("hook_event_name", "PreToolUse")
    tool_name = event.get("tool_name", "?")
    tool_input = event.get("tool_input", {}) or {}
    cwd = event.get("cwd", "")
    session_id = (event.get("session_id", "") or "")[:8] or "?"
    correlation_id = uuid.uuid4().hex[:12]

    summary = short_summary(tool_name, tool_input)
    cwd_label = os.path.basename(cwd.rstrip("/")) or cwd or "?"
    title = f"Claude Code: approve {tool_name}?"
    body = f"{summary}\n\ncwd: {cwd_label}  ·  session: {session_id}"

    priority, tag = risk_level(tool_name, tool_input)

    actions = "; ".join(
        [
            f"http, Allow, {ntfy_base}/{resp_topic}, method=POST, body=allow:{correlation_id}",
            f"http, Deny,  {ntfy_base}/{resp_topic}, method=POST, body=deny:{correlation_id}",
            f"http, Always,{ntfy_base}/{resp_topic}, method=POST, body=always:{correlation_id}",
        ]
    )

    headers = {
        "Title": title,
        "Priority": priority,
        "Tags": tag,
        "Actions": actions,
    }
    if auth:
        headers["Authorization"] = auth

    try:
        post(f"{ntfy_base}/{perm_topic}", body, headers, timeout=5)
    except Exception as e:
        sys.stderr.write(f"ntfy_permission: publish failed: {e}\n")
        sys.exit(0)

    deadline = time.time() + timeout
    since = int(time.time())
    decision = None  # 'allow' | 'deny' | 'always'

    while time.time() < deadline and decision is None:
        remaining = max(1, int(deadline - time.time()))
        url = f"{ntfy_base}/{resp_topic}/json?poll=1&since={since}"
        req = urllib.request.Request(url)
        if auth:
            req.add_header("Authorization", auth)
        try:
            with urllib.request.urlopen(req, timeout=min(5, remaining)) as resp:
                for line in resp:
                    if not line.strip():
                        continue
                    try:
                        msg = json.loads(line)
                    except Exception:
                        continue
                    if msg.get("event") != "message":
                        continue
                    since = max(since, int(msg.get("time", since)) + 1)
                    text = msg.get("message", "") or ""
                    if not text.endswith(f":{correlation_id}"):
                        continue
                    if text.startswith("allow:"):
                        decision = "allow"
                    elif text.startswith("deny:"):
                        decision = "deny"
                    elif text.startswith("always:"):
                        decision = "always"
                    break
        except urllib.error.URLError:
            pass
        if decision is None:
            time.sleep(2)

    if decision == "allow":
        emit_decision(event_name, "allow", f"via ntfy ({correlation_id})")
    elif decision == "deny":
        emit_decision(event_name, "deny", f"via ntfy ({correlation_id})")
    elif decision == "always":
        pattern = always_pattern(tool_name, tool_input)
        added = append_to_allow(pattern) if pattern else False
        reason = (
            f"via ntfy: always-allow {pattern} added to permissions"
            if added
            else f"via ntfy ({correlation_id})"
        )
        emit_decision(event_name, "allow", reason)
    sys.exit(0)


if __name__ == "__main__":
    main()
