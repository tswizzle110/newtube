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
  to open its page, where you can sort by **Newest / Popular / Oldest**. YouTube ignores sort options on a
  channel's Videos tab and only pages forwards, so Popular and Oldest read the channel's **whole upload list
  once** (about 100 videos per request, paced; a 3,000-video channel takes roughly 30 seconds, with a progress
  bar; you can leave the page and it carries on). The list is then **kept** in the database (about 170 bytes per
  video: 3,000 videos is ~0.5 MB), so sorting, paging (60 at a time, loaded as you scroll) and switching
  between Popular and Oldest are instant and exact. Opening a channel after a few hours reads just its newest
  page (one request) to add new uploads, and every 30 days the full list is re-read in the background
  (that refreshes the view counts Popular uses). Only channels above 10,000 videos are limited (Popular
  becomes "most viewed among the newest 10,000", Oldest is unavailable). "Rescan" at the bottom of the list
  re-reads it on demand.
  The ☰ button hides the sidebar; `/` brings it back and focuses the search box.
- **Quality:** the quality button offers every resolution the video has, up to 2160p (4K). Above 1080p
  YouTube only serves VP9/AV1, which need more computer power than H.264: **Settings → Playback** lets you
  choose the codec and cap the highest resolution (e.g. 1080p if 4K stutters on your machine).
- **Keys:** `?` in the player lists every shortcut (space/k, j/l, arrows, `f`, `c`, `<` `>`, `{` `}` for
  chapters, `[` `]` to mark a SponsorBlock segment, `u` to undo a skip).

## Importing your Google history (Takeout)

This gives the recommender something to learn from on day one. It reads a file you downloaded yourself;
newtube never logs in to Google.

1. At <https://takeout.google.com> choose **only YouTube and YouTube Music**. Under *All YouTube data included*
   select **history**, **playlists** and **subscriptions**. Under *Multiple formats* set **history to JSON**
   (the default is HTML, which cannot be read). Export once, as a `.zip`.
2. Copy the zip to the server (`scp takeout-*.zip server:~/`). You do not need to unpack it; an unpacked folder
   works too.
3. Look before you import. This only reads, writes nothing, and prints counts, date ranges, and the *shape* of
   any file it could not use (key names and types, never your history):

   ```bash
   cd ~/newtube && uv run newtube takeout inspect ~/takeout-*.zip
   ```

4. Rehearse. A dry run does the whole import and then undoes it, so the numbers are exact:

   ```bash
   uv run newtube takeout import ~/takeout-*.zip --dry-run
   ```

5. Import for real. It is all-or-nothing (a failure leaves the database untouched) and safe to repeat: running
   it again, or with a newer export that overlaps, only adds what is new.

   ```bash
   uv run newtube takeout import ~/takeout-*.zip
   ```

Options: `--skip watch,search,subscriptions,likes,watch-later,playlists` leaves parts out;
`--include-music` also counts YouTube Music plays (off by default); `--likes NAME` / `--watch-later NAME` say
which playlist file is which if your export's language is one it could not recognise.

What is skipped, and why, is listed in the report: removed videos, ads, community posts and other Google
products have no usable video, so they are counted but not stored. Imported items arrive with only an id (and
the title and channel the history gave); the full details are filled in later, slowly, within the request budget.

### Filling in the details (automatic)

Imported videos start with just an id. While `newtube serve` is running, it fetches the details (title, channel,
length, date) slowly in the background: about one video a minute, never more than `backfill_daily_cap` (default
300) in any 24 hours, and only when no subscribed channel is waiting for its update. Liked videos go first, then
watch later, then videos you watched more than once, then the rest, newest first. Private or deleted videos are
remembered and not asked for again. If YouTube ever asks for a bot check, the backfill pauses for 6 hours.

```bash
uv run newtube backfill status
```

shows what is left and how long it should take. Set `backfill_daily_cap = 0` in `newtube.toml` to turn it off.
These videos do not appear in your Subscriptions feed (that is for recent uploads); the recommender uses them.

## "For you" (recommendations)

The **For you** tab next to Subscriptions ranks videos from your subscriptions (last 45 days) and your Watch
later list. It reads only your own database: it never contacts YouTube. Each video scores the sum of a few
transparent terms, and **Why this?** under a card shows every term with its points and the total:

| Term | Meaning |
| --- | --- |
| You watch *channel* a lot / have watched before | Time-decayed interest from your history, likes and watch-later (half-life 180 days). Opening a video in imported history counts a little; real playback in newtube counts by how much you watched. |
| Uploaded *N* ago | Newer scores higher (half-life 7 days). |
| From a channel you subscribe to | A flat bonus. |
| In your Watch later | A flat bonus. |
| You started this and did not finish | Videos you stopped partway in newtube come back. |
| Shown *N* times without a click | A penalty for videos you keep scrolling past (last 14 days). |
| *N* higher videos from *channel* already shown | Stops one prolific channel filling the page. |

Never shown: videos you finished or watched before (unless you stopped partway in newtube), videos you marked
**Not interested**, and channels you marked **Never this channel**. Both buttons are on every For-you card and
are remembered (locally). **Refresh** re-ranks using what you have done since.

The weights are the `Weights` class in `newtube/recommend/baseline.py`. This is the hand-tuned baseline that a
learned model will later have to beat in the offline evaluation.

## Backups

Your history, imports and likes live in one file, `data/newtube.db`. Take a safe copy any time, even while
newtube is running:

```bash
cd ~/newtube && uv run newtube backup          # -> data/backups/newtube-YYYYMMDD-HHMMSS.db (UTC)
uv run newtube backup --list
```

Each copy is made with SQLite's online backup, checked (`integrity_check`) before it is kept, and readable by you
only. The newest 14 are kept (`backup_keep`; `backup_dir` changes the folder: set it to a different disk if you can).

**Nightly, automatically** (a systemd timer; if the machine was off at 03:20 it runs when it next starts):

```bash
cp deploy/newtube-backup.service deploy/newtube-backup.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now newtube-backup.timer
systemctl --user list-timers newtube-backup        # when it runs next
journalctl --user -u newtube-backup                # what the last run said
```

A copy on the same disk does not survive that disk failing. Now and then copy `data/backups` somewhere else
(another machine, an external drive); the files are private to you but **not encrypted**, so treat them like the
database itself.

**Restoring** (this replaces the current database with the copy):

```bash
systemctl --user stop newtube
cp ~/newtube/data/newtube.db ~/newtube-before-restore.db      # keep what you have, just in case
rm -f ~/newtube/data/newtube.db-wal ~/newtube/data/newtube.db-shm
cp ~/newtube/data/backups/newtube-YYYYMMDD-HHMMSS.db ~/newtube/data/newtube.db
systemctl --user start newtube
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
- Every outside connection the server makes (including yt-dlp's) is recorded; see "Checking it yourself" below.
- Watch history, impressions and other signals live only in `data/newtube.db`.
- SponsorBlock: looking up segments sends only a 4-character hash prefix (the server can't tell which
  video). Submitting a segment or voting does reveal the video, so it is **off by default** (Settings).
  Its random anonymous ID is stored in the local database and is never sent to your browser. The
  optional "viewed" pings the official extension sends on every skip are not implemented.

## Embeddings ("similar to what you watch")

Each video gets a vector describing what it is about (from its title, channel, category, tags and the start of
the description). Your taste is the average of the videos you watched, liked or saved, and a candidate scores
higher the closer it is to that. It shows in **Why this?** as "Similar to videos you have watched". All of it
happens on this machine; nothing is sent anywhere.

**It already works with nothing installed**, using a simple built-in embedder (it compares shared words). Videos
are embedded in the background while the server is idle; check progress with:

```bash
uv run newtube ml status
```

**For much better results, use a real language model on the T600.** The server never downloads anything, so you
fetch the model files yourself, once. My suggestion is the multilingual MiniLM (about 470 MB, 384 dimensions, 50+
languages); I could not check the name or size from my sandbox, so confirm them on huggingface.co first. On any
computer with internet:

```bash
pip install huggingface_hub
huggingface-cli download sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 \
    --local-dir paraphrase-multilingual-MiniLM-L12-v2
scp -r paraphrase-multilingual-MiniLM-L12-v2 you@server:~/newtube/data/models/
```

Then on the server:

```bash
cd ~/newtube && git pull
uv sync --extra ml                       # PyTorch + sentence-transformers (several GB; one time)
echo 'embedding_model = "paraphrase-multilingual-MiniLM-L12-v2"' >> newtube.toml
uv run newtube ml check                  # should say "on cuda"; "on cpu" means the GPU was not found
uv run newtube ml embed                  # embed everything now (otherwise it happens in the background)
systemctl --user restart newtube
```

The model is loaded from that folder only, with Hugging Face's offline mode forced on, so it cannot download
anything or phone home; `newtube privacy-check` will show if it ever tried. Changing `embedding_model` simply
re-embeds everything with the new model (vectors from different models are never compared). Remove the line to go
back to the built-in embedder.

## Checking it yourself: `privacy-check`

Our own HTTP client refuses hosts that are not on the allowlist, but yt-dlp does its own networking. So the server
also watches its **own sockets**: every DNS lookup and every outside connection made by any Python code in the
server, yt-dlp included, is noted. (Connections to this machine and private networks are ignored. yt-dlp's
JavaScript solver, Deno, runs with `--no-remote` and no network permission, and the server starts no other
programs.)

**Test it now.** This runs the selftest's real requests (video info, streams, search, channels, comments,
SponsorBlock, thumbnails) with the watcher on, then lists every host contacted:

```bash
cd ~/newtube && uv run newtube privacy-check
```

Expected: only `*.youtube.com`, `*.googlevideo.com`, `*.ytimg.com`, `*.ggpht.com`/`googleusercontent.com` and
`sponsor.ajay.app`, ending in "every one on the allowlist". Anything else is listed as **NOT ON THE ALLOWLIST** and
the command exits with status 1. If that happens, send me the output: it means yt-dlp wants a host we have not
approved, and we decide together whether to allow it or stop it.

**See what the running server has done** (the table is kept in `data/newtube.db`, with first/last times):

```bash
uv run newtube privacy-check --log
```

**Then lock it.** Once you are happy with the list, set `egress_mode = "enforce"` in `newtube.toml` and restart.
From then on, a DNS lookup or connection to anything off the allowlist is **refused** (the name is checked before
the lookup, so no DNS query is even sent), and the refusal is logged and shown in `--log`. The cost: if YouTube
one day needs a new host, that feature fails until you add the host (`ALLOWED_HOST_SUFFIXES` in
`newtube/net/http.py`) — the log tells you which. `egress_mode = "off"` disables the watcher.

With `outbound_proxy` set, every connection goes to the proxy, so the real destinations are not visible to this
check; audit the proxy (e.g. gluetun's firewall/logs) instead.

## Tests

```bash
uv run pytest                 # unit + API tests, no network needed
```

`e2e/README.md` describes the real-browser playback test.
