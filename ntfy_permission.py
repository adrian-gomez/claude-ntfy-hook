#!/usr/bin/env python3
"""
Claude Code PreToolUse hook: route permission decisions through an ntfy push
notification with Allow / Deny action buttons.

Behavior:
- Publishes a notification to NTFY_PERM_TOPIC describing the tool call.
- Waits up to NTFY_TIMEOUT seconds for a response on NTFY_RESP_TOPIC.
- If the response matches our correlation id, returns "allow" or "deny".
- On timeout / any error, emits no decision so Claude Code falls back to its
  native terminal prompt — the script never *blocks* a tool call by accident.

Required env:
  NTFY_PERM_TOPIC       outbound topic (you subscribe on Android)
  NTFY_RESP_TOPIC       inbound topic (action buttons publish here)

Optional env:
  NTFY_BASE             default https://ntfy.sh
  NTFY_TIMEOUT          seconds to wait, default 30 (settings.json hook timeout
                        should be slightly higher)
  NTFY_AUTH             optional 'Bearer tk_...' or 'Basic <base64>' header
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


def env_required(name):
    v = os.environ.get(name, "").strip()
    if not v:
        sys.stderr.write(f"ntfy_permission: {name} not set; falling through\n")
        sys.exit(0)
    return v


def truncate(s, lim):
    s = s if isinstance(s, str) else json.dumps(s, separators=(",", ":"))
    return s if len(s) <= lim else s[: lim - 1] + "…"


def short_summary(tool_name, tool_input):
    if tool_name == "Bash":
        cmd = tool_input.get("command", "")
        return truncate(cmd, 600)
    if tool_name in ("Edit", "Write", "NotebookEdit"):
        path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
        return truncate(path, 400)
    parts = [f"{k}={truncate(v, 100)}" for k, v in tool_input.items()]
    return truncate(", ".join(parts), 600)


def post(url, data, headers, timeout=5):
    req = urllib.request.Request(
        url,
        data=data.encode("utf-8") if isinstance(data, str) else data,
        headers=headers,
        method="POST",
    )
    return urllib.request.urlopen(req, timeout=timeout).read()


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

    tool_name = event.get("tool_name", "?")
    tool_input = event.get("tool_input", {}) or {}
    cwd = event.get("cwd", "")
    correlation_id = uuid.uuid4().hex[:12]

    summary = short_summary(tool_name, tool_input)
    title = f"Claude Code: approve {tool_name}?"
    body = f"{summary}\n\ncwd: {os.path.basename(cwd) or cwd}"

    actions = "; ".join(
        [
            f"http, Allow, {ntfy_base}/{resp_topic}, method=POST, body=allow:{correlation_id}",
            f"http, Deny,  {ntfy_base}/{resp_topic}, method=POST, body=deny:{correlation_id}",
        ]
    )

    headers = {
        "Title": title,
        "Priority": "high",
        "Tags": "lock",
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
    decision = None

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
                    text = msg.get("message", "")
                    if not text.endswith(f":{correlation_id}"):
                        continue
                    if text.startswith("allow:"):
                        decision = "allow"
                    elif text.startswith("deny:"):
                        decision = "deny"
                    break
        except urllib.error.URLError:
            pass
        if decision is None:
            time.sleep(2)

    if decision:
        event_name = event.get("hook_event_name", "PreToolUse")
        if event_name == "PermissionRequest":
            payload = {
                "hookSpecificOutput": {
                    "hookEventName": "PermissionRequest",
                    "decision": {"behavior": decision},
                }
            }
        else:
            payload = {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": decision,
                    "permissionDecisionReason": f"via ntfy ({correlation_id})",
                }
            }
        print(json.dumps(payload))
    sys.exit(0)


if __name__ == "__main__":
    main()
