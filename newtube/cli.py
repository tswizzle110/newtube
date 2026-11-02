"""Command line entry point: ``newtube <command>``."""

from __future__ import annotations

import argparse
import sys

from . import __version__


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="newtube")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("serve", help="run the web app")
    st = sub.add_parser("selftest", help="check that YouTube extraction works from this machine")
    st.add_argument("--dump", metavar="DIR", help="save redacted raw extractor output here "
                                                  "(handy for tests / bug reports)")
    sub.add_parser("passwd", help="set the login password")
    tk = sub.add_parser("takeout", help="import a Google Takeout (watch history, subscriptions, likes...)")
    tsub = tk.add_subparsers(dest="takeout_command", required=True)
    ins = tsub.add_parser("inspect", help="describe an export; reads it, writes nothing")
    imp = tsub.add_parser("import", help="import an export into the database (all or nothing)")
    for sp in (ins, imp):
        sp.add_argument("paths", nargs="+", help="the Takeout .zip file(s), or the folder they were unpacked to")
        sp.add_argument("--include-music", action="store_true", help="also count YouTube Music plays (skipped by default)")
        sp.add_argument("--likes", metavar="NAME", help="part of the file name of your liked-videos playlist (if not detected)")
        sp.add_argument("--watch-later", metavar="NAME", help="part of the file name of your watch-later playlist (if not detected)")
    imp.add_argument("--dry-run", action="store_true", help="do everything, report exact numbers, keep nothing")
    imp.add_argument("--skip", default="", metavar="PARTS",
                     help="comma-separated parts to leave out: watch,search,subscriptions,likes,watch-later,playlists")
    bf = sub.add_parser("backfill", help="progress of filling in details for imported videos")
    bf.add_argument("backfill_command", choices=["status"], nargs="?", default="status")
    bk = sub.add_parser("backup", help="save a safe copy of the database (works while newtube is running)")
    bk.add_argument("--dir", metavar="DIR", help="where to put it (default: backup_dir in newtube.toml, else data/backups)")
    bk.add_argument("--keep", type=int, metavar="N", help="how many copies to keep (default: backup_keep, 14)")
    bk.add_argument("--list", action="store_true", help="list the copies instead of making one")
    pc = sub.add_parser("privacy-check", help="show every outside host this server contacts (and prove nothing else is)")
    pc.add_argument("--log", action="store_true",
                    help="show what the running server has contacted so far, instead of testing now")
    ml = sub.add_parser("ml", help="text embeddings for 'similar to what you watch'")
    ml.add_argument("ml_command", choices=["status", "check", "embed"], nargs="?", default="status",
                    help="status: counts; check: load the model and time it (shows GPU/CPU); embed: embed everything pending now")
    sub.add_parser("migrate", help="apply database migrations")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # Commands are wired up in later commits; imported lazily so `--help` is instant.
    if args.command == "serve":
        from .server import serve

        return serve()
    if args.command == "selftest":
        from .selftest import run

        return run(dump_dir=args.dump)
    if args.command == "passwd":
        return _passwd()
    if args.command == "takeout":
        return _takeout(args)
    if args.command == "ml":
        return _ml(args)
    if args.command == "privacy-check":
        return _privacy_check(args)
    if args.command == "backup":
        return _backup(args)
    if args.command == "backfill":
        return _backfill_status()
    if args.command == "migrate":
        from .db.connection import migrate_cli

        return migrate_cli()
    return 2


def _ml(args: argparse.Namespace) -> int:
    import time

    from .config import load_settings
    from .db.connection import connect, migrate
    from .ml.embedder import ModelNotInstalled, load_embedder
    from .ml.store import count_pending, embed_pending

    s = load_settings()
    conn = connect(s.db_path)
    migrate(conn)
    try:
        emb = load_embedder(s)
    except ModelNotInstalled as e:
        print(e)
        return 2
    device = getattr(emb, "device", "cpu (built-in word-hashing embedder: no model, no GPU)")
    if args.ml_command == "check":
        t0 = time.time()
        vecs = emb.embed(["a quick test of the embedding model"] * 64)
        dt = time.time() - t0
        print(f"model {emb.name} on {device}: {len(vecs[0])} dimensions, 64 texts in {dt:.2f}s")
        return 0
    if args.ml_command == "embed":
        done, t0 = 0, time.time()
        while (n := embed_pending(conn, emb, 256)):
            done += n
            print(f"\r{done:,} embedded", end="", flush=True)
        print(f"\nEmbedded {done:,} video(s) in {time.time() - t0:.1f}s with {emb.name}.")
        return 0
    have = conn.execute("SELECT count(*) FROM video_embeddings WHERE model=?", (emb.name,)).fetchone()[0]
    other = conn.execute("SELECT count(*) FROM video_embeddings WHERE model<>?", (emb.name,)).fetchone()[0]
    print(f"Embedder: {emb.name} on {device}")
    print(f"Videos embedded: {have:,}   waiting: {count_pending(conn, emb.name):,}"
          + (f"   (+{other:,} from another model, re-done when reached)" if other else ""))
    return 0


def _privacy_check(args: argparse.Namespace) -> int:
    from .config import load_settings
    from .net import audit

    s = load_settings()
    if args.log:
        from .db.connection import connect, migrate

        conn = connect(s.db_path)
        migrate(conn)
        entries = audit.load_entries(conn)
        if not entries:
            print("Nothing recorded yet. The running server records every outside connection (egress_mode is "
                  f"{s.egress_mode!r}); use it for a while, then run this again.")
            return 0
        print(audit.format_report(entries, with_dates=True))
        return 1 if audit.violations(entries) else 0

    # Active check: do what selftest does (real requests to YouTube, SponsorBlock, thumbnails, streams...) with
    # the audit watching every socket, and report who was contacted.
    from .selftest import run

    a = audit.install(audit.EgressAudit("record", (_proxy_host(s),) if s.outbound_proxy else ()))
    code = run(dump_dir=None)
    entries = a.snapshot()
    print("\n== Outside connections made during that test ==\n")
    print(audit.format_report(entries))
    if s.outbound_proxy:
        print("\nNote: outbound_proxy is set, so every connection goes to the proxy and the real destinations are"
              " not visible from here.")
    print("\nThis covers all of this server's Python code, including yt-dlp. yt-dlp's JavaScript solver (Deno) runs"
          " with --no-remote and no network permission, and nothing else is started.")
    return 1 if audit.violations(entries) else code


def _proxy_host(s) -> str:
    from urllib.parse import urlsplit

    return urlsplit(s.outbound_proxy).hostname or ""


def _backup(args: argparse.Namespace) -> int:
    from pathlib import Path

    from .backup import BackupError, list_backups, make_backup
    from .config import load_settings
    from .db.connection import connect

    s = load_settings()
    dest = Path(args.dir).expanduser() if args.dir else s.backups_path
    if args.list:
        found = list_backups(dest)
        for p in found:
            print(f"{p}  {p.stat().st_size / 1e6:.1f} MB")
        print(f"{len(found)} backup(s) in {dest}")
        return 0
    if not s.db_path.exists():
        print(f"No database at {s.db_path}: nothing to back up.")
        return 2
    try:
        b = make_backup(connect(s.db_path), dest, args.keep if args.keep else s.backup_keep)
    except BackupError as e:
        print(e)
        return 1
    print(f"Saved {b.path}  ({b.size / 1e6:.1f} MB; {b.events:,} events, {b.videos:,} videos; checked OK)")
    if b.pruned:
        print(f"Removed {len(b.pruned)} older backup(s).")
    print("Keep a copy somewhere other than this machine too: a disk failure would take both.")
    return 0


def _backfill_status() -> int:
    from .config import load_settings
    from .db.connection import connect, migrate
    from .jobs.backfill import progress

    s = load_settings()
    conn = connect(s.db_path)
    migrate(conn)
    p = progress(conn, s.backfill_daily_cap)
    print(f"Imported videos still waiting for details: {p['waiting']:,}  (+ {p['waiting_later']:,} to retry later)")
    print(f"Done: {p['done']:,}   Private/deleted/given up: {p['gone']:,}")
    if p["daily_cap"] <= 0:
        print("Backfill is turned off (backfill_daily_cap = 0).")
    else:
        print(f"Fetched in the last 24 hours: {p['used_last_day']:,} of {p['daily_cap']:,} allowed")
        if p["waiting"]:
            days = -(-p["waiting"] // p["daily_cap"])
            print(f"At this rate the rest takes about {days} day(s), while `newtube serve` is running.")
    return 0


def _passwd() -> int:
    import getpass

    from .auth import Auth
    from .config import load_settings

    auth = Auth(load_settings().data_dir / "auth.json")
    pw = getpass.getpass("New password (min 8 chars): ")
    if pw != getpass.getpass("Repeat: "):
        print("Passwords differ.")
        return 1
    try:
        auth.set_password(pw)
    except ValueError as e:
        print(e)
        return 1
    print("Password set. All existing sessions are logged out.")
    return 0


def _takeout(args: argparse.Namespace) -> int:
    from .importers.takeout import PARTS, NullSink, Options, format_report, read_takeout, run_import

    skip = {s.strip() for s in getattr(args, "skip", "").split(",") if s.strip()}
    if skip - set(PARTS):
        print(f"Unknown part(s) to skip: {', '.join(sorted(skip - set(PARTS)))}. Choose from: {', '.join(PARTS)}")
        return 2
    opts = Options(include_music=args.include_music, skip=skip, likes=args.likes, watch_later=args.watch_later)

    def progress(msg: str) -> None:
        print(msg, file=sys.stderr, flush=True)

    try:
        if args.takeout_command == "inspect":
            print(format_report(read_takeout(args.paths, NullSink(), opts, progress)))
            return 0
        from .config import load_settings
        from .db.connection import connect, migrate

        conn = connect(load_settings().db_path)
        migrate(conn)
        rep = run_import(conn, args.paths, opts, dry_run=args.dry_run, progress=progress)
        print(format_report(rep, wrote=not args.dry_run))
        if not args.dry_run:
            print("\nDone. Run it again any time (or with a newer export): nothing is imported twice.")
        return 0
    except FileNotFoundError as e:
        print(e)
        return 2


if __name__ == "__main__":
    sys.exit(main())
