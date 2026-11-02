"""A record of every outside host this server talks to, and (optionally) a lock on the allowlist.

Why: our own HTTP client refuses hosts that are not on the allowlist, but yt-dlp does its own networking and
could, in principle, contact other hosts. Everything that touches the network here is Python code inside this
process (yt-dlp's JavaScript solver, Deno, runs with ``--no-remote`` and no network permission, and we start no
other programs), so watching the process's sockets sees all of it.

How: ``socket.getaddrinfo`` (name lookups) and ``socket.socket.connect`` (connections) are wrapped.
Connections to this machine or private networks are ignored. A connection is attributed to the name it was
resolved from; a connection to an address that was never resolved by name is reported by its address and counts
as NOT allowed.

Modes (setting ``egress_mode``):
    record   (default) note everything, block nothing, so you can see what really happens first
    enforce  refuse lookups and connections to anything off the allowlist (the DNS name is checked BEFORE the
             lookup, so nothing is even asked of a DNS server)
    off      do nothing

With ``outbound_proxy`` set, every connection goes to the proxy and the real destinations are invisible here
(the proxy is the thing to audit then); the proxy host itself counts as allowed.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import sqlite3
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from .http import host_allowed

log = logging.getLogger("newtube.audit")


class EgressBlocked(OSError):
    """Enforce mode refused a connection to a host that is not on the allowlist."""


@dataclass
class Entry:
    host: str
    port: int
    kind: str                # 'connect' | 'lookup'
    allowed: bool
    blocked: int = 0
    count: int = 0
    first_seen: int = 0
    last_seen: int = 0
    last_ip: str | None = None


def _is_public(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip.split("%")[0]).is_global
    except ValueError:
        return False


def _is_ip_literal(name: str) -> bool:
    try:
        ipaddress.ip_address(name.split("%")[0])
        return True
    except ValueError:
        return False


def _local_name(name: str) -> bool:
    """Names that never leave this machine/network: localhost, bare host names, IP literals (judged by address)."""
    return name == "localhost" or "." not in name or _is_ip_literal(name)


class EgressAudit:
    def __init__(self, mode: str = "record", extra_hosts: tuple[str, ...] = ()) -> None:
        if mode not in ("record", "enforce"):
            raise ValueError(f"egress mode must be 'record' or 'enforce', not {mode!r}")
        self.mode = mode
        self.extra = {h.lower() for h in extra_hosts if h}
        self._lock = threading.Lock()
        self._names: dict[str, str] = {}              # address -> the name it was last resolved from
        self._entries: dict[tuple[str, int, str], Entry] = {}
        self._pending: dict[tuple[str, int, str], Entry] = {}      # not yet written to the database
        self._warned: set[str] = set()

    def host_ok(self, host: str) -> bool:
        return host in self.extra or host_allowed(host)

    # ---- recording ------------------------------------------------------------------------------
    def _note(self, host: str, port: int, kind: str, allowed: bool, blocked: bool, ip: str | None) -> None:
        now = int(time.time())
        key = (host, port, kind)
        with self._lock:
            for store in (self._entries, self._pending):
                e = store.get(key)
                if e is None:
                    e = store[key] = Entry(host, port, kind, allowed, first_seen=now)
                e.count += 1
                e.blocked += int(blocked)
                e.last_seen, e.last_ip = now, ip or e.last_ip
        if not allowed and host not in self._warned:
            self._warned.add(host)
            log.warning("%s %s %s (not on the allowlist)", "BLOCKED" if blocked else "contacted", kind, host)

    def snapshot(self) -> list[Entry]:
        with self._lock:
            return sorted((Entry(**vars(e)) for e in self._entries.values()), key=lambda e: (e.allowed, e.host, e.kind))

    def flush(self, conn: sqlite3.Connection) -> int:
        """Add what happened since the last flush to the database's totals. Returns the rows written."""
        with self._lock:
            pending, self._pending = list(self._pending.values()), {}
        for e in pending:
            conn.execute(
                "INSERT INTO egress_log(host,port,kind,allowed,blocked,count,first_seen,last_seen,last_ip)"
                " VALUES (?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(host,port,kind) DO UPDATE SET count=count+excluded.count,"
                "  blocked=blocked+excluded.blocked, allowed=excluded.allowed, last_seen=excluded.last_seen,"
                "  last_ip=COALESCE(excluded.last_ip, last_ip)",
                (e.host, e.port, e.kind, int(e.allowed), e.blocked, e.count, e.first_seen, e.last_seen, e.last_ip))
        return len(pending)

    # ---- the hooks ------------------------------------------------------------------------------
    def before_lookup(self, name: str) -> None:
        """Enforce mode: refuse a disallowed name BEFORE it is looked up (a DNS query would itself tell someone)."""
        if self.mode == "enforce" and not _local_name(name) and not self.host_ok(name):
            self._note(name, 0, "lookup", False, True, None)
            raise socket.gaierror(socket.EAI_NONAME, f"blocked by newtube egress policy: {name}")

    def after_lookup(self, name: str, results: list) -> None:
        if _local_name(name):
            return
        public = [r[4][0] for r in results if _is_public(r[4][0])]
        if not public:
            return
        with self._lock:
            for ip in public:
                self._names[ip] = name
        self._note(name, 0, "lookup", self.host_ok(name), False, public[0])

    def on_connect(self, ip: str, port: int) -> None:
        if not _is_public(ip):
            return
        host = self._names.get(ip, ip)
        allowed = self.host_ok(host)
        blocked = self.mode == "enforce" and not allowed
        self._note(host, port, "connect", allowed, blocked, ip)
        if blocked:
            raise EgressBlocked(f"blocked by newtube egress policy: {host}:{port}")


# ---------------------------------------------------------------------------- installing the hooks

_active: EgressAudit | None = None
_originals: dict[str, object] = {}


def active() -> EgressAudit | None:
    return _active


def install(audit: EgressAudit) -> EgressAudit:
    """Wrap the socket functions (once). Call ``uninstall`` to put the originals back."""
    global _active
    if _active is not None:
        uninstall()
    orig_getaddrinfo, orig_connect, orig_connect_ex = socket.getaddrinfo, socket.socket.connect, socket.socket.connect_ex
    _originals.update(getaddrinfo=orig_getaddrinfo, connect=orig_connect, connect_ex=orig_connect_ex)

    def getaddrinfo(host, port, *args, **kwargs):
        name = (host.decode("idna") if isinstance(host, bytes) else host or "")
        name = name.lower().rstrip(".") if isinstance(name, str) else ""
        if name:
            audit.before_lookup(name)
        results = orig_getaddrinfo(host, port, *args, **kwargs)
        if name:
            audit.after_lookup(name, results)
        return results

    def _check(sock: socket.socket, address) -> None:
        if sock.family in (socket.AF_INET, socket.AF_INET6) and isinstance(address, tuple) and len(address) >= 2:
            audit.on_connect(str(address[0]), int(address[1]))

    def connect(self, address):
        _check(self, address)
        return orig_connect(self, address)

    def connect_ex(self, address):
        try:
            _check(self, address)
        except EgressBlocked:
            return 1                      # EPERM, the way connect_ex reports a failure
        return orig_connect_ex(self, address)

    socket.getaddrinfo = getaddrinfo
    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    _active = audit
    return audit


def uninstall() -> None:
    global _active
    if _originals:
        socket.getaddrinfo = _originals["getaddrinfo"]
        socket.socket.connect = _originals["connect"]
        socket.socket.connect_ex = _originals["connect_ex"]
        _originals.clear()
    _active = None


def install_from_settings(settings) -> EgressAudit | None:
    mode = settings.egress_mode
    if mode == "off":
        return None
    proxy = urlsplit(settings.outbound_proxy).hostname if settings.outbound_proxy else None
    return install(EgressAudit(mode, (proxy,) if proxy else ()))


# ---------------------------------------------------------------------------- reporting

def violations(entries: list[Entry]) -> list[Entry]:
    return [e for e in entries if not e.allowed]


def format_report(entries: list[Entry], *, with_dates: bool = False) -> str:
    if not entries:
        return "No outside connections recorded."
    width = max(len(e.host) for e in entries) + 2
    head = f"{'HOST':<{width}}{'WHAT':<9}{'PORT':>5}{'TIMES':>8}  VERDICT"
    lines = [head, "-" * len(head)]
    for e in entries:
        verdict = "ok" if e.allowed else ("BLOCKED, NOT ON THE ALLOWLIST" if e.blocked and e.blocked >= e.count else "NOT ON THE ALLOWLIST")
        what = "connect" if e.kind == "connect" else "dns"
        port = str(e.port) if e.kind == "connect" else ""
        extra = ""
        if with_dates:
            extra = f"   {time.strftime('%Y-%m-%d %H:%M', time.localtime(e.first_seen))} .. {time.strftime('%Y-%m-%d %H:%M', time.localtime(e.last_seen))}"
        lines.append(f"{e.host:<{width}}{what:<9}{port:>5}{e.count:>8}  {verdict}{extra}")
    bad = violations(entries)
    hosts = {e.host for e in entries}
    lines.append("")
    if bad:
        lines.append(f"{len({e.host for e in bad})} host(s) are NOT on the allowlist: " + ", ".join(sorted({e.host for e in bad})))
    else:
        lines.append(f"{len(hosts)} outside host(s), every one on the allowlist.")
    return "\n".join(lines)


def load_entries(conn: sqlite3.Connection) -> list[Entry]:
    return sorted((Entry(r["host"], r["port"], r["kind"], bool(r["allowed"]), r["blocked"], r["count"],
                         r["first_seen"], r["last_seen"], r["last_ip"])
                   for r in conn.execute("SELECT * FROM egress_log")), key=lambda e: (e.allowed, e.host, e.kind))
