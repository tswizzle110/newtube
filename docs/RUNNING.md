# Running newtube

## One-time setup (Ubuntu Server)

```bash
sudo apt update && sudo apt install -y git ffmpeg curl unzip nodejs npm
```

`uv` (Python packages/venv) and Deno (the JavaScript runtime yt-dlp needs) come from their
official installers; answer **Y** when Deno asks to add itself to your PATH:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
curl -fsSL https://deno.land/install.sh | sh
```

Open a new shell, then check `uv --version`, `deno --version`, `node --version` (18 or newer).

## Get the code and build

The repo is private, so `git clone` asks for a username and a **fine-grained token** with
read-only *Contents* access to this one repository (GitHub no longer accepts passwords).

```bash
git clone https://github.com/tswizzle110/newtube.git ~/newtube
cd ~/newtube
git checkout claude/laughing-allen-8q25cq
uv sync
(cd frontend && npm install && npm run build)
```

## Check that YouTube extraction works from this machine

```bash
uv run newtube selftest
```

Every line should say `[ OK ]`. If not, see "When YouTube breaks things" below.

## First run

```bash
uv run newtube passwd          # choose the login password (stored only as a scrypt hash)
uv run newtube serve           # then open http://<server>:8787
```

Open the port only on your LAN or Tailscale; this app is not meant to be exposed publicly.
To confirm the port is free first: `ss -ltnp | grep ':8787' || echo free`.

## Run it as a service (starts at boot)

```bash
mkdir -p ~/.config/systemd/user && cp deploy/newtube.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now newtube
loginctl enable-linger "$USER"
journalctl --user -u newtube -f        # logs
```

## Updating

```bash
cd ~/newtube && git pull
uv sync && (cd frontend && npm install && npm run build)
systemctl --user restart newtube
```

## Using it

- **Sidebar:** the navigation is on the left. Click **Subscriptions** to see your feed and open your
  channel list underneath it (the arrow ▸ opens/closes the list without leaving the page). Click a channel
  to open its page, where you can sort by **Newest / Popular / Oldest**. Popular and Oldest are built from the
  channel's uploads list (up to 500 videos, read once and cached), because YouTube ignores sort options on the
  Videos tab: Popular is "most viewed among the newest 500", and Oldest is only offered for channels with
  500 videos or fewer (the page says so when it isn't).
  The ☰ button hides the sidebar; `/` brings it back and focuses the search box.
- **Quality:** the quality button offers every resolution the video has, up to 2160p (4K). Above 1080p
  YouTube only serves VP9/AV1, which need more computer power than H.264: **Settings → Playback** lets you
  choose the codec and cap the highest resolution (e.g. 1080p if 4K stutters on your machine).
- **Keys:** `?` in the player lists every shortcut (space/k, j/l, arrows, `f`, `c`, `<` `>`, `{` `}` for
  chapters, `[` `]` to mark a SponsorBlock segment, `u` to undo a skip).

## When YouTube breaks things

Most breakages are fixed by a newer yt-dlp:

```bash
uv lock --upgrade-package yt-dlp && uv sync && uv run newtube selftest
systemctl --user restart newtube
```

If `selftest` still fails, send me the output. Extraction code is confined to
`newtube/extract/ytdlp.py`.

## Configuration

Copy `newtube.toml.example` to `newtube.toml` (git-ignored) or set `NEWTUBE_*` environment
variables. To send all outbound traffic through a proxy later, set `outbound_proxy`: one
setting covers the app and yt-dlp.

## IPv4 or IPv6

`selftest` prints the address YouTube saw (`exit_ip`). If it is an IPv6 address, that is your server's own
address rather than your router's shared IPv4 one. To force a family, put this in `newtube.toml`
(ignored when `outbound_proxy` is set, because then the proxy decides):

```toml
ip_version = "4"        # "auto" (default), "4" or "6"
```

## Hiding your home IP: route everything through gluetun / a VPN

Everything the server sends to YouTube goes through one setting, `outbound_proxy`, and this is
covered by tests: the app's HTTP client, video streaming and yt-dlp all use it, and hostnames are
sent to the proxy (DNS is resolved on the VPN side). Gluetun has a built-in HTTP proxy:

1. In your gluetun service enable it and publish the port **on localhost only** (the proxy has no
   password by default, so never publish it to your LAN). Check gluetun's wiki for the current
   variable names; at the time of writing:

   ```yaml
   services:
     gluetun:
       environment:
         - HTTPPROXY=on
         - HTTPPROXY_LISTENING_ADDRESS=:8888
         # optional: - HTTPPROXY_USER=newtube   - HTTPPROXY_PASSWORD=...
       ports:
         - 127.0.0.1:8888:8888/tcp
   ```

   Recreate gluetun, then restart any containers that use `network_mode: service:gluetun`
   (they lose their network when gluetun is recreated).

2. In `~/newtube/newtube.toml`:

   ```toml
   outbound_proxy = "http://127.0.0.1:8888"      # with a password: "http://user:pass@127.0.0.1:8888"
   ```

3. Restart newtube and run `uv run newtube selftest`. The `exit_ip` line shows the address YouTube
   saw; it must differ from your home IP. If the VPN drops, gluetun's kill switch makes requests
   fail instead of leaking.

Things to know: VPN addresses are shared and often flagged, so YouTube may ask "Sign in to confirm
you're not a bot" more often than on a home connection. If `selftest` fails with that message, pick
a different PIA region in gluetun (`SERVER_REGIONS`). Video data also flows through the VPN, so its
speed limits playback quality. To undo: remove the `outbound_proxy` line and restart.

## Privacy checklist (what is enforced)

- The browser only talks to this server (CSP `default-src 'self'`; thumbnails, avatars, captions
  and video are all proxied; no web fonts).
- With `outbound_proxy` set, all of that traffic leaves through the proxy (see above).
- The server only contacts youtube.com, googlevideo.com, ytimg.com, ggpht.com,
  googleusercontent.com and sponsor.ajay.app (SponsorBlock).
- Watch history, impressions and other signals live only in `data/newtube.db`.
- SponsorBlock: looking up segments sends only a 4-character hash prefix (the server can't tell which
  video). Submitting a segment or voting does reveal the video, so it is **off by default** (Settings).
  Its random anonymous ID is stored in the local database and is never sent to your browser. The
  optional "viewed" pings the official extension sends on every skip are not implemented.

## Tests

```bash
uv run pytest                 # unit + API tests, no network needed
```

`e2e/README.md` describes the real-browser playback test.
