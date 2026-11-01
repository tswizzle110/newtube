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
    sub.add_parser("selftest", help="check that YouTube extraction works from this machine")
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

        return run()
    if args.command == "migrate":
        from .db.connection import migrate_cli

        return migrate_cli()
    return 2


if __name__ == "__main__":
    sys.exit(main())
