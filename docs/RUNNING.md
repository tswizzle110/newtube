# Running newtube on the ThinkStation

## One-time setup

```bash
sudo apt update && sudo apt install -y git ffmpeg curl unzip
```

```bash
# uv (Python package/venv manager), installer from astral.sh
curl -LsSf https://astral.sh/uv/install.sh | sh
```

```bash
# Deno (JavaScript runtime yt-dlp needs), installer from deno.com
curl -fsSL https://deno.land/install.sh | sh
```

Open a new shell (or `source ~/.bashrc`) and check `uv --version` and `deno --version`.
If `deno` isn't found, add `export PATH="$HOME/.deno/bin:$PATH"` to `~/.bashrc`.

## Get the code

```bash
git clone https://github.com/tswizzle110/newtube.git
cd newtube
git checkout claude/laughing-allen-8q25cq
uv sync
```

## Check that extraction works (do this first)

```bash
uv run newtube selftest --dump ./selftest-dump
```

Each line is `[ OK ]` or `[FAIL]` with timing and detail. Paste the whole output back.
`selftest-dump/` holds the raw yt-dlp output with URL tokens stripped; no personal data.

## Keeping it working

When YouTube changes something and extraction breaks:

```bash
uv lock --upgrade-package yt-dlp && uv sync && uv run newtube selftest
```

## Configuration

Optional `newtube.toml` in the project directory, or `NEWTUBE_*` environment variables
(env wins). See `newtube/config.py` for every setting. Example:

```toml
outbound_proxy = ""          # e.g. "socks5://127.0.0.1:1080" for all outbound traffic
js_runtime = "deno"          # or "node", "bun", "quickjs"
```
