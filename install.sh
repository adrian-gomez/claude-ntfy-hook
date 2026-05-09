#!/usr/bin/env bash
# Bootstrap claude-ntfy-hook: generate topics, symlink the script, print the
# settings.json snippet to merge by hand. Idempotent.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_DIR="${CLAUDE_HOME:-$HOME/.claude}"
SCRIPT_LINK="$CLAUDE_DIR/scripts/ntfy_permission.py"
ENV_FILE="$REPO_DIR/.env.topics"

mkdir -p "$CLAUDE_DIR/scripts"

ln -sf "$REPO_DIR/ntfy_permission.py" "$SCRIPT_LINK"
chmod +x "$REPO_DIR/ntfy_permission.py"
echo "Symlinked $SCRIPT_LINK -> $REPO_DIR/ntfy_permission.py"

if [[ ! -f "$ENV_FILE" ]]; then
  perm="claude-perm-$(openssl rand -hex 12)"
  resp="claude-resp-$(openssl rand -hex 12)"
  cat > "$ENV_FILE" <<EOF
export NTFY_PERM_TOPIC=$perm
export NTFY_RESP_TOPIC=$resp
EOF
  echo "Generated topics in $ENV_FILE"
fi

cat <<'EOF'

----------------------------------------------------------------
Next steps:

1. Subscribe on your phone (ntfy app) to the perm topic in $ENV_FILE.
   Smoke test from a terminal:
     . "$ENV_FILE"
     curl -d "ntfy works" https://ntfy.sh/$NTFY_PERM_TOPIC

2. Source the env vars from your shell rc:
     echo "[ -f $ENV_FILE ] && source $ENV_FILE" >> ~/.zshrc

3. Merge this into ~/.claude/settings.json (under existing 'hooks'):

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

4. Open a new terminal, run `claude`, ask it to do something that needs
   permission (e.g. write a file under /tmp). Your phone should ping.
----------------------------------------------------------------
EOF
