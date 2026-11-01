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

## Privacy checklist (what is enforced)

- The browser only talks to this server (CSP `default-src 'self'`; thumbnails, avatars, captions
  and video are all proxied; no web fonts).
- The server only contacts youtube.com, googlevideo.com, ytimg.com, ggpht.com,
  googleusercontent.com and sponsor.ajay.app (SponsorBlock arrives in phase 2).
- Watch history, impressions and other signals live only in `data/newtube.db`.

## Tests

```bash
uv run pytest                 # unit + API tests, no network needed
```

`e2e/README.md` describes the real-browser playback test.
