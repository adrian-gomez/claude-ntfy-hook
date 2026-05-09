# Self-hosted ntfy

By default, claude-ntfy-hook posts to public `ntfy.sh`. That means tool names, command summaries, and `cwd` basenames transit Heckel's servers in plaintext. Fine for personal projects; not great for client codebases or anything covered by an NDA.

This directory contains a minimal Docker setup for running your own ntfy server. Three deployment paths, sized to the privacy bar you actually need.

## Path A — LAN-only (5 min)

Easiest. ntfy listens on your local network, no auth. Anyone on your home/office WiFi could find topics by guessing — fine if that's your threat model.

```sh
cd selfhost
docker compose up -d
curl -d "smoke test" http://<your-mac-ip>:8080/test   # subscribe to "test" on phone, should buzz
```

In your shell rc:

```sh
export NTFY_BASE=http://<your-mac-ip>:8080
export NTFY_PERM_TOPIC=claude-perm-<random>
export NTFY_RESP_TOPIC=claude-resp-<random>
```

In the ntfy app on your phone, change the default server in Settings → Default server to `http://<your-mac-ip>:8080`, then subscribe.

## Path B — Tailscale (5 min, if you already use it)

ntfy runs on a Tailscale-enabled host. Only your tailnet (your devices + people you invite) can reach it.

1. Install the [Tailscale macOS app](https://tailscale.com/download/macos), `tailscale up`.
2. Install Tailscale on your phone, log in to the same tailnet.
3. `docker compose up -d` from this dir on your Mac.
4. Find the Mac's tailnet hostname: `tailscale status` → e.g. `mac-mini.your-tailnet.ts.net`.
5. Set `NTFY_BASE=http://mac-mini.your-tailnet.ts.net:8080` and the ntfy app's default server to the same URL.

No auth required because Tailscale is the auth boundary.

## Path C — Public hostname with auth (~15 min)

Public-internet reachable, with proper auth tokens.

1. Point a domain at the host running Docker, get HTTPS (Caddy / nginx / Cloudflare Tunnel).
2. Edit `server.yml`:
   - Change `base-url` to your `https://` URL.
   - Change `auth-default-access` to `deny-all`.
3. `docker compose up -d`.
4. Create an admin and grant access to the topics:
   ```sh
   docker exec -it ntfy ntfy user add --role=admin admin
   # prompts for password
   docker exec -it ntfy ntfy access admin "claude-perm-*" rw
   docker exec -it ntfy ntfy access admin "claude-resp-*" rw
   ```
5. Generate a long-lived access token:
   ```sh
   docker exec -it ntfy ntfy token add admin
   # prints tk_...
   ```
6. In your shell rc:
   ```sh
   export NTFY_BASE=https://ntfy.your-domain.com
   export NTFY_AUTH="Bearer tk_..."
   export NTFY_PERM_TOPIC=claude-perm-<random>
   export NTFY_RESP_TOPIC=claude-resp-<random>
   ```
7. In the ntfy app, set default server to your URL and add the access token under Settings → Manage users.

## Files

- `docker-compose.yml` — minimal compose; mounts `./cache`, `./etc`, and `./server.yml`. Health-checked.
- `server.yml` — ntfy config (default: `auth-default-access: read-write` for path A; comments show the toggles for path C).
- `cache/`, `etc/` — runtime state, created on first `docker compose up`. Already in `.gitignore`.

## Notes

- Phones need to be able to reach the server. If that's intermittent (laptop closes the lid, mac sleeps), public ntfy.sh is more reliable than self-hosted unless you keep the server on something always-on (NAS, Raspberry Pi, VPS).
- Path A is fine even without auth because the topic names are themselves long random strings — see the main README's "Privacy" section.
- ntfy supports HTTP/2 and WebSocket subscriptions. The hook script uses HTTP polling (`/json?poll=1&since=...`), which works against any ntfy version including self-hosted.
