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
length, date) slowly in the background: spread across the day (30 a day means one about every 48 minutes), never more than `backfill_daily_cap` (default
30) in any 24 hours, and only when no subscribed channel is waiting for its update. Liked videos go first, then
watch later, then videos you watched more than once, then the rest, newest first. Private or deleted videos are
remembered and not asked for again. If YouTube ever asks for a bot check, the backfill pauses for 6 hours.

```bash
uv run newtube backfill status
```

shows what is left and how long it should take. Set `backfill_daily_cap = 0` in `newtube.toml` to turn it off.
These videos do not appear in your Subscriptions feed (that is for recent uploads); the recommender uses them.

## If YouTube says "Sign in to confirm you're not a bot"

YouTube sometimes decides that the address a request comes from (your server's IP) looks automated, and refuses to hand
out videos until it calms down. It has nothing to do with your login (newtube has none). What newtube does now:

* After the first refusal it **stops asking** instead of making it worse. Background work (backfill, related videos,
  channel-page checks) stays away for 6 hours, then 12, then 24 if it is refused again; what you click gets a single
  attempt about once a minute, and in between fails instantly with a plain message and **Try again** (nothing retries a
  bot check by itself). The moment YouTube relents, playback works again and background work resumes.
* The background defaults are gentler (`backfill_daily_cap = 30`, `related_daily_cap = 10`).

**A quiet profile** (what a single person's server can sustain without drawing attention; every number is a ceiling):

```toml
backfill_daily_cap = 30      # imported videos looked up per day, spread out: one about every 48 minutes (0 while flagged)
related_daily_cap = 10       # related-video lookups per day: one about every 2.4 hours (0 while flagged)
rss_poll_interval = 14400    # check each channel's feed every 4 hours (default 3): 6 checks a day per channel
min_request_interval = 2.0   # seconds between YouTube requests (default 0.8)
request_jitter = 2.0         # plus up to this much random extra (default 0.6)
```

Why these: YouTube judges the *rate and shape* of requests from one address. What counts as expensive is a full
video/channel lookup (yt-dlp), not a feed check or a thumbnail. newtube spreads the capped work over the whole day
(a cap of 30 is not 30 in half an hour), limits the "channel page" lookup that replaces a failing feed to 6 an hour
and revisits a channel with a broken feed only twice a day (before this, a feed outage meant hundreds of full lookups in
a few minutes). What you do yourself (opening videos, comments, searches) is not limited: a person clicking is normal.
Allow roughly `channels x 24 / rss_poll_interval_in_hours + 150` for `background_daily_cap` if you lower that too.
While you are flagged, set both caps to 0 and wait; once it has been quiet for a week, bring them back to the numbers above.

**Everything newtube asks YouTube for, and what limits it** (the full list; a test checks that every route into YouTube
obeys the bot gate, so a future change cannot quietly bypass it):

| What | Kind | What limits it |
| --- | --- | --- |
| Feed check per channel | cheap request | once per `rss_poll_interval`; pauses 30 min after 3 failures in a row |
| Channel-page lookup, only when a feed is broken | **heavy** | 6 an hour (counted in the database, failures included); a broken-feed channel is revisited every 12 h |
| Backfill of imported videos | **heavy** | `backfill_daily_cap` a day, spread over the day; counted in the database |
| Related lists for recommendations | **heavy** | `related_daily_cap` a day, spread over the day; counted in the database |
| Channel pictures (sidebar) | background | one channel lookup per subscription that has no picture, within `channel_picture_daily_cap` a day (default 40, spread over the day: about one every 36 minutes; 0 turns it off); each channel is tried once, then not again for 3 days |
| Opening a video (info, Up next, comments, search, channel page) | **heavy** | only what you click; results are cached (video 4 h, related 3 days, search 1 h, channel 15 min); a failed lookup is remembered for 45 s so retries cost nothing |
| Thumbnails and channel pictures | image server | fetched once, then kept in the disk cache |
| Video data | video servers | only while you watch |
| `selftest` | ~20 **heavy** | only when you run it; `--only video_info` runs just one |

**Safety nets** that apply to every heavy lookup: after a bot check the gate (above) closes and *stays closed across restarts*
(it is kept in the database, so a restart or a crash loop cannot ask again); a failing lookup is not repeated for 45 seconds;
and `selftest` shares the gate, so it will not hammer YouTube while you are flagged.

**What to do about it, in this order:**

1. **Give it time and quiet.** Turn the background lookups off while it clears (often hours, sometimes a day or two):
   set `backfill_daily_cap = 0` and `related_daily_cap = 0` in `newtube.toml`, restart newtube, and avoid hammering Try again.
2. **Update yt-dlp**, which tracks YouTube's changes (it is often the actual fix):
   `uv lock --upgrade-package yt-dlp && uv sync`, restart newtube, then check gently with `uv run newtube selftest --only video_info`
   (the full `selftest` makes about 20 lookups in a minute: save that for when things are working again).
3. **Change the address YouTube sees:** send the server's traffic through a VPN/proxy (the gluetun route in "Hiding your
   home IP"). A fresh address is not flagged. This is the most reliable fix for a persistent flag.
4. **Slow down** a little more (`min_request_interval = 2.0`, `request_jitter = 1.5`) if it keeps coming back.

**Not offered, on purpose:** logging in or passing browser cookies, which is what yt-dlp's message suggests. It would
tie every request to your Google account, which is exactly what newtube exists to avoid. (An optional "PO token"
helper program for yt-dlp is another route; it is an extra program to install and trust, so ask me before adding it.)

## Closing the laptop, switching wifi, and other interruptions

newtube is built to carry on when your connection comes and goes:

* **Video:** if the picture freezes because the connection dropped (wifi switched, laptop woke from sleep, the server
  restarted), the player says so ("Offline…" / "reconnecting…"), keeps trying, and carries on from the same spot by
  itself, without reloading the page. After about three minutes of failures it stops and shows **Try again**.
* **What you watched and did:** the browser keeps anything it could not send (history, watch time, likes... all of it) on
  disk, sends it again as soon as the connection is back (and when you reopen newtube if you closed the tab meanwhile),
  and each report is dated when it *happened*, not when it got through, so History and Watch time stay right. The
  server counts each report once even if the reply was lost on the way.
* **Pages:** loading a page while offline, or while the server is restarting, waits and retries instead of showing an
  error; a banner says what is going on; pages that failed reload themselves when the connection returns; and a page
  you left open for 30+ minutes reloads when you come back to it.
* **The server:** one failing background job can no longer stop the others (feed polling, backfill...), a dead
  connection to YouTube is retried once on a fresh one, and restarts finish within seconds even mid-video.
  The service file (`deploy/newtube.service`) now restarts it always, after 3 seconds, without limit. After updating
  it: `cp deploy/newtube.service ~/.config/systemd/user/ && systemctl --user daemon-reload && systemctl --user restart newtube`.

What it cannot do: if the *server* is off or unreachable (for example Tailscale is not connected on the laptop), nothing
can load; newtube waits and picks up as soon as it is reachable again. Your login lasts 30 days.

## On a phone, and "install" it

newtube works in a phone browser: on a narrow screen the sidebar becomes a slide-over menu (☰, top left) that closes
when you choose something, pages fit the screen, and the player is built for touch (big buttons; the first tap on a
playing video brings the controls back, the next tap pauses; double-tap fullscreen is a mouse-only habit and is off).
The mini-player works there too.

**Installing it like an app** (its own icon on the home screen, opening without the browser's address bar):

* **iPhone / iPad (Safari):** Share → **Add to Home Screen**.
* **Android (Chrome):** menu → **Add to Home screen** / **Install app**.

Chrome only offers the full "Install app" (and treats the page as an installable app) on a secure (`https://`)
address. Reaching the server by its plain `http://100.x.x.x:8787` Tailscale address is *not* a secure context for
Chrome, so you will get an ordinary shortcut instead of an installed app. For the real thing, let Tailscale give the
server an HTTPS address (check Tailscale's current docs: roughly `tailscale serve --bg 8787`, with HTTPS enabled for
your tailnet), set `host = "127.0.0.1"` in `newtube.toml`, and use the `https://<machine>.<tailnet>.ts.net`
address. It stays private to your tailnet; nothing is exposed to the internet.

There is deliberately **no offline mode** (no service worker): newtube is a streaming client, an offline copy of the
page would only go stale, and it would be one more thing to break. The icon files are generated by
`python3 scripts/make_icons.py` (no image libraries needed).

## Theater mode and the mini-player

**Theater mode** (the ▭ button in the player, or `t`) gives the video the whole width of the window and moves
Up next below it. It is remembered.

**Mini-player:** if you leave the video page (search, open a channel, go Home...) while a video is playing, it does
not stop: it shrinks into a small player in the bottom-right corner and carries on, with no restart and the same
position. Hover it for a title bar with **⤢** (back to the full video page, still playing) and **✕** (close it).
Opening any other video replaces it. The mini-player ignores keyboard shortcuts, so Space and the arrow keys still
scroll the page you are on. A playlist or list keeps advancing to the next video in the mini-player too.

## Thumbnails

Video cards use the sharpest picture YouTube has for each video: the 1280x720 one when it exists, otherwise the
480x360 one (the small Up next thumbnails stay small). Each is fetched once from `i.ytimg.com` by your server and
kept in the image cache (`cache_max_bytes`, 3 GiB by default, roughly 20,000 thumbnails at this quality), so your
browser never contacts YouTube. If you are on a slow or metered connection and want the old small thumbnails
back, change `'best'` to `'mqdefault'` in `frontend/src/components/VideoCard.svelte` and rebuild.

## Search filters

A video search has three filters under the tabs: **Uploaded** (last hour, today, this week, this month, this year),
**Length** (under 4 minutes, 4 to 20 minutes, over 20 minutes) and **Sort by** (relevance, upload date, view count).
They are passed to YouTube the way its own site does (the `sp` parameter), they are part of the page address (so a
filtered search can be bookmarked), and your search log records which ones you used. They apply to video searches
only. `uv run newtube selftest` has a **search_filters** check that asks for "over 20 minutes" and confirms the
results really are long, so a change on YouTube's side would show up there.

## Channel pictures

The round pictures in the sidebar come from the channel's own page. A subscription added without them (an import, or an older
version) gets its picture filled in slowly in the background, one lookup at a time (`channel_picture_daily_cap`, on the
Settings page; see the request table in the bot-check section). To get them sooner, **Manage channels** shows how many are missing and a **Fetch them now** button: it looks up to 15 per press,
one after another, as ordinary channel lookups (stopping at once if YouTube asks for a bot check); press again for the next 15.
Until then, and if a picture ever fails to load, the
channel's letter is shown. A stored picture address that YouTube no longer serves (403, 404 or 410) is forgotten and looked
up again the same way.

## Channel playlists

A channel page has **Videos** and **Playlists** tabs. Playlists shows the playlists the channel itself has made (its
Playlists tab on YouTube); opening one plays it like any other playlist. It costs one ordinary channel lookup (the kind
opening the channel page makes), when you click the tab, and the list is then kept for six hours.

## Up next (related videos)

Beside the video you are watching (below it on a narrow window) is **Up next**: videos related to it, taken from
YouTube's own "Mix" for that video. The list is requested a moment after the video starts so it never delays
playback, kept in your database for 3 days, and a failed lookup is retried after an hour (or at once with **Try again**).
On current yt-dlp versions the Mix address is read as one ordinary video, not a playlist, so there is no Mix to read.
newtube notices this (one wasted lookup), remembers it for a week (also across restarts), and uses the title search
described next. After a yt-dlp update it tries a Mix again once the week is up.

**Only the same subject.** A title search returns anything that shares words with the title, so newtube keeps only
results about the same subject (`newtube/services/relevance.py`). It takes the words that define the video (its title and
tags, minus filler such as "how", "why", "best", "tutorial", numbers; tags count extra, because that is where creators
name the genre) and requires a result to share enough of them. A result from a channel you subscribe to or watch is judged
one step more leniently, and one from the video's own channel always counts. **Settings → Recommendations → "How closely
Up next must match the video"** picks *loose*, *normal* (default) or *strict*; it applies to videos you open from then on.
With a real language model configured (see Embeddings) it also has to be close in meaning, which separates genres much
better than words can: the built-in word-hashing method cannot (it sees "starters" in a sourdough video and a Python one
as the same word), so it is not used for this. If a video has too few matches, Up next falls back to the channel's other
videos and similar titles from your library, and shows fewer rather than unrelated ones.

If YouTube's Mix comes back empty, Up next **searches YouTube for the video's title** instead (one ordinary search request,
like using the search box) and shows those results. After three empty Mixes in a row it stops asking for Mixes for six
hours and searches directly, so a Mix that has stopped working costs one request per video, not two.

When YouTube's list is not available (a bot check, a failed lookup, or nothing returned) Up next is filled from your
own library instead, and says so: the channel's other videos this server already knows, then the closest titles by
the same embeddings as "similar to what you watch". That needs no request to YouTube at all.

The same lists feed **For you**. A slow background job fetches the related list of the videos you like most
(liked, watched often and recently, on your Watch later) so that videos from channels you do **not** subscribe to
can be recommended, with the reason "Related to videos you have watched". It makes at most `related_daily_cap`
lookups per 24 hours (default 10; 0 turns it off), spread across the day, only when nothing else is waiting, and
never when YouTube has asked for a bot check. This is extra traffic to YouTube from your IP (one request per
lookup, to `www.youtube.com`, the same kind of request as opening a playlist), so it is yours to turn down.

`uv run newtube selftest` has a new **related** check; if it fails, send me the output (and
`selftest --dump DIR` output) so the parser can be fixed against real data.

## Watch time

**Watch time** (sidebar, Library) shows how much you have actually watched in newtube: a total, the average per day,
your longest day, a bar for every day (days you did not watch are listed as "–"), and your most-watched channels and
videos. It counts only real playback, as *video* time (at 2x speed an hour of video takes half an hour of your day but
counts as an hour); skipped parts are not counted, and replaying the same minute inside one viewing is not counted
twice. Two separate viewings of the same video both count. Your imported Google history is **not** included (it only
says a video was opened, not for how long), and watches you remove from History are removed from the totals too.

**Skipped by SponsorBlock** is totalled underneath (and shown next to each day): the length of every sponsor, intro,
etc. that was skipped, automatically or with the Skip button, minus skips you undid. It is read from the skips newtube
already logged, so skips from before this was added count too. It is time *saved*, so it is not part of watch time;
skipping the same sponsor again after rewinding counts again, and "jump to highlight" is not a skip.

It counts from your **first recorded watch** unless you set **Counting from** to a date (for example the day you
started), which is remembered; **Back to automatic** undoes that. "A day" is a calendar day in your browser's time
zone, and a viewing counts on the day it started.

## History

**History** (sidebar, Library) lists every video you watched, newest first and grouped by day: your imported
Google history plus what you watch in newtube. Search it by title or channel. **Remove from history** forgets that
you watched a video, and where you stopped: it stops showing as "Watched before", stops counting towards
recommendations, and can be recommended again. Removing is permanent even if you import a newer Takeout later
(only watches *after* the removal count again). The underlying event log is not rewritten.

## Your lists: Like, Watch later

Under every video are **Like** and **Watch later** buttons. They put the video on your own **Liked videos** or
**Watch later** list (the *Library* section of the sidebar; each list shows its count). Everything is stored only
in your database: nothing is sent to YouTube, and your YouTube account is not touched.

- Your imported likes and Watch later items from the Takeout are already on these lists.
- **Play all** on a list plays it in order and moves on by itself when a video ends.
- **Your own playlists:** the **Save** menu under a video adds it to any playlist you have (or **Create & save**
  a new one). The **＋** next to *Library* in the sidebar makes an empty playlist. On a playlist's page you can
  **Rename** it, **Delete** it (its videos are not affected), and reorder with the ↑ / ↓ buttons; **Play all**
  follows that order. Names must be unique (ignoring capitals). A video can be on any number of playlists.
- The recommender uses them: liked videos raise the channels and subjects you like for as long as they stay on
  the list (remove one and it stops counting), and Watch later items are recommended until you watch or remove them.

## Home and Subscriptions

**Home** (the first tab, and the first item in the sidebar) is the mixed front page: a shelf of **New from your
subscriptions** (your newest unwatched uploads, with *See all*), then **Recommended for you**, ranked from your
subscriptions, Watch later and videos related to what you like. **Subscriptions** is the plain chronological feed
of your channels, as before. In **Settings → Start page** you choose which one newtube opens on (remembered by
that browser only); Home is always one click away.

## How recommendations are ranked

The **Recommended for you** list on Home ranks videos from five places, all already in your own database (it never
contacts YouTube): your subscriptions' uploads (last 45 days), your Watch later list, videos related to ones you
like, other videos from channels you watch but do not subscribe to (last year), and the videos whose title and topic
are closest to your taste (the embeddings below). At least **40%** of the page is kept for the last four whenever there
are enough of them, so uploads from your subscriptions cannot fill it on their own. Home loads the best 240 and shows
48 at a time ("Show more"). Each video scores the sum of a few
transparent terms, and **Why this?** under a card shows every term with its points and the total:

| Term | Meaning |
| --- | --- |
| You watch *channel* a lot / have watched before | Time-decayed interest from your history, likes and watch-later (half-life 180 days). Opening a video in imported history counts a little; real playback in newtube counts by how much you watched. |
| Uploaded *N* ago | Newer scores higher (half-life 7 days). |
| From a channel you subscribe to | A small flat bonus (0.4; the channels' uploads are most of the candidates anyway). |
| In your Watch later | A flat bonus. |
| You started this and did not finish | A weak nudge (0.3) so videos you stopped partway can come back without taking over. |
| Shown *N* times without a click | A penalty for videos you keep scrolling past (last 14 days). |
| *N* higher videos from *channel* already shown | Stops one prolific channel filling the page. |

Never shown: videos you finished or watched before (unless you stopped partway in newtube), videos you marked
**Not interested**, and channels you marked **Never this channel**. Both buttons are on every For-you card and
are remembered (locally). **Refresh** re-ranks using what you have done since.

The weights are the `Weights` class in `newtube/recommend/baseline.py` (`explore_share` is the 40%; set it to 0 to rank
purely by score). This is the hand-tuned baseline that a
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

### Changing settings from the page (no file editing)

**Settings** has a *YouTube traffic and system* section with the settings people most often change, each with a plain
explanation and sensible limits:

* **Presets** fill in the form for you (nothing changes until you press **Save**): **Quiet profile** (the numbers from
  "If YouTube says not a bot"), **Pause background lookups** (both caps to 0), and **Built-in defaults**.
* A green **applies now** tag means the change takes effect the moment you save (the lookup caps, how often channels are
  checked, the pause between requests, the background ceiling). An amber **after restart** tag means newtube must restart
  (proxy, protocol, the outside-connection record, the thumbnail cache size, the language model). A saved one waiting for a
  restart is marked, and a **Restart newtube now** button appears if newtube was started as a service (otherwise the page
  tells you the command).
* The top of the section shows what has been used against each limit (and says so if YouTube is asking for a bot check).
* A wrong value is explained next to its box and *nothing at all* is saved until everything is right.
* **use default** puts one setting back to what `newtube.toml` (or the built-in value) gives.

What you save is kept in the database and used by the server and by every command (`backup`, `selftest`...). The order is:
built-in default, then `newtube.toml`, then what you saved on the page; an environment variable (`NEWTUBE_<NAME>`) beats all
of them and is shown as locked. The address, port and folders are deliberately file-only: changing them from a page served on
that very address could lock you out.

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

## Apple TV, Safari and AirPlay (HLS)

The web player uses DASH, which Apple's players cannot play. For them the server also offers HLS, built from the
same streams with no re-encoding:

- `/api/hls/<video id>.m3u8`: the entry point (one variant per resolution, H.264 + AAC only; VP9 and AV1 are never
  offered because AVPlayer cannot play them, so the ceiling is whatever H.264 YouTube serves, normally 1080p).
- `/api/hls/<video id>/<format>.m3u8`: one track's fragment list, read from the stream's own index.

Segments are byte ranges of the same `/api/stream/...` proxy the web player uses, so the privacy rules are unchanged.
A 404 means the video has no H.264/AAC pair. This is tested against hand-built playlists, **not yet against a real
Apple device**: on a Mac or iPhone, log in to newtube in Safari, then open `http://<server>:<port>/api/hls/<id>/play` (a bare page with one
native video, which also has the AirPlay button). Do not open the `.m3u8` itself in Safari: it hands that to a system
viewer that does not send your login cookie.

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
