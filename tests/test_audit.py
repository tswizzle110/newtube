"""The egress audit: sees yt-dlp-style connections, ignores local ones, and (optionally) refuses off-list hosts.

The real socket functions are replaced by fakes before the audit is installed, so no test touches the network."""
import asyncio
import socket
import threading

import pytest

from newtube.config import load_settings
from newtube.db.connection import connect, migrate
from newtube.net import audit as au

# Real-looking public addresses (the sockets are faked, so nothing is contacted). 203.0.113.x would NOT do:
# it is a reserved documentation range and the audit rightly treats it as not public.
YT, GV, EVIL, SELF = "142.250.80.1", "142.250.80.2", "93.184.216.34", "93.184.216.99"
DNS = {"www.youtube.com": YT, "rr1---sn-x.googlevideo.com": GV, "evil.example": EVIL, "notyoutube.com": EVIL,
       "youtube.com.attacker.net": EVIL, "localhost": "127.0.0.1", "nas.lan.example": "192.168.1.5"}


@pytest.fixture
def fakes(monkeypatch):
    """Fake resolver + connector underneath the audit; records what actually got through."""
    calls = {"lookups": [], "connects": []}

    def fake_getaddrinfo(host, port, *a, **k):
        calls["lookups"].append(host)
        if host not in DNS:
            raise socket.gaierror(socket.EAI_NONAME, "no such host")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (DNS[host], port or 0))]

    def fake_connect(self, address):
        calls["connects"].append(address)

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", fake_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", lambda self, address: calls["connects"].append(address) or 0)
    yield calls
    au.uninstall()


def go(mode="record", extra=()):
    return au.install(au.EgressAudit(mode, extra))


def fetch(host, port=443):
    """What urllib3 / http.client do: create_connection."""
    s = socket.create_connection((host, port))
    s.close()


def rows(a):
    return {(e.host, e.kind): e for e in a.snapshot()}


# ---- allowlist matching -----------------------------------------------------------------------------

@pytest.mark.parametrize("host,ok", [
    ("youtube.com", True), ("www.youtube.com", True), ("rr1---sn-x.googlevideo.com", True), ("i.ytimg.com", True),
    ("sponsor.ajay.app", True), ("notyoutube.com", False), ("youtube.com.attacker.net", False),
    ("evil.example", False), ("YouTube.COM.", True), ("googleapis.com", False)])
def test_host_allowed(host, ok):
    from newtube.net.http import host_allowed
    assert host_allowed(host) is ok


# ---- recording --------------------------------------------------------------------------------------

def test_a_connection_is_attributed_to_the_name_it_was_resolved_from(fakes):
    a = go()
    fetch("www.youtube.com"); fetch("rr1---sn-x.googlevideo.com")
    r = rows(a)
    assert r[("www.youtube.com", "connect")].allowed and r[("www.youtube.com", "connect")].port == 443
    assert r[("rr1---sn-x.googlevideo.com", "connect")].allowed and r[("www.youtube.com", "lookup")].allowed
    assert au.violations(a.snapshot()) == []
    assert fakes["connects"] == [(YT, 443), (GV, 443)]                    # and they went through


def test_record_mode_notes_an_off_list_host_but_lets_it_through(fakes):
    a = go("record")
    fetch("evil.example")
    bad = au.violations(a.snapshot())
    assert {e.host for e in bad} == {"evil.example"} and all(e.blocked == 0 for e in bad)
    assert (EVIL, 443) in fakes["connects"]


def test_look_alike_domains_are_flagged(fakes):
    a = go()
    fetch("notyoutube.com"); fetch("youtube.com.attacker.net")
    assert {e.host for e in au.violations(a.snapshot())} == {"notyoutube.com", "youtube.com.attacker.net"}


def test_a_connection_to_an_address_nobody_looked_up_is_reported_and_not_allowed(fakes):
    a = go()
    s = socket.socket(); s.connect((SELF, 443)); s.close()
    (bad,) = au.violations(a.snapshot())
    assert (bad.host, bad.kind, bad.allowed) == (SELF, "connect", False)


def test_this_machine_and_private_networks_are_ignored(fakes):
    a = go()
    for host in ("localhost", "nas.lan.example"):
        fetch(host)
    for ip in ("127.0.0.1", "192.168.1.5", "10.0.0.2", "100.64.1.9", "169.254.1.1"):
        s = socket.socket(); s.connect((ip, 80)); s.close()
    a.on_connect("::1", 80); a.on_connect("fe80::1%eth0", 80); a.on_connect("fd00::5", 80)    # IPv6 local, link-local, private
    assert a.snapshot() == [] and len(fakes["connects"]) == 7             # all went through, none recorded


def test_the_asyncio_path_is_covered_too(fakes):
    a = go()

    async def main():
        loop = asyncio.get_running_loop()
        await loop.getaddrinfo("www.youtube.com", 443)
        s = socket.socket(); s.setblocking(False)
        await loop.sock_connect(s, (YT, 443))
        s.close()
    asyncio.run(main())
    assert rows(a)[("www.youtube.com", "connect")].count == 1


def test_connections_from_other_threads_are_counted(fakes):
    a = go()
    ts = [threading.Thread(target=fetch, args=("www.youtube.com",)) for _ in range(8)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert rows(a)[("www.youtube.com", "connect")].count == 8


# ---- enforcing --------------------------------------------------------------------------------------

def test_enforce_refuses_an_off_list_name_before_any_lookup(fakes):
    a = go("enforce")
    with pytest.raises(socket.gaierror, match="blocked by newtube egress policy"):
        fetch("evil.example")
    assert "evil.example" not in fakes["lookups"] and fakes["connects"] == []        # nothing was even asked of DNS
    (e,) = au.violations(a.snapshot())
    assert e.blocked == 1 and e.kind == "lookup"


def test_enforce_refuses_a_raw_address_that_was_never_resolved(fakes):
    go("enforce")
    s = socket.socket()
    with pytest.raises(au.EgressBlocked):
        s.connect((SELF, 443))
    assert s.connect_ex((SELF, 443)) == 1 and fakes["connects"] == []
    s.close()


def test_enforce_still_allows_the_allowlist_and_local_traffic(fakes):
    a = go("enforce")
    fetch("www.youtube.com"); fetch("rr1---sn-x.googlevideo.com"); fetch("localhost")
    assert au.violations(a.snapshot()) == [] and len(fakes["connects"]) == 3


def test_a_proxy_host_counts_as_allowed(fakes):
    a = go("enforce", extra=("evil.example",))             # (as if outbound_proxy pointed at it)
    fetch("evil.example")
    assert au.violations(a.snapshot()) == []


def test_uninstall_puts_the_originals_back(fakes):
    before = (socket.getaddrinfo, socket.socket.connect, socket.socket.connect_ex)
    go(); au.uninstall()
    assert (socket.getaddrinfo, socket.socket.connect, socket.socket.connect_ex) == before and au.active() is None


def test_real_loopback_traffic_is_untouched_and_unrecorded():
    srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1)
    a = go("enforce")
    try:
        c = socket.create_connection(srv.getsockname()); c.close()
        assert a.snapshot() == []
    finally:
        au.uninstall(); srv.close()


# ---- persistence and reporting ----------------------------------------------------------------------

@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.db"); migrate(c)
    return c


def test_totals_accumulate_across_flushes_and_restarts(fakes, conn):
    a = go()
    fetch("www.youtube.com"); fetch("evil.example")
    assert a.flush(conn) == 4 and a.flush(conn) == 0                       # a lookup and a connect for each of 2 hosts
    fetch("www.youtube.com")
    a.flush(conn)
    b = go()                                                               # a restart: new object, same database
    fetch("www.youtube.com")
    b.flush(conn)
    got = {(e.host, e.kind): e for e in au.load_entries(conn)}
    assert got[("www.youtube.com", "connect")].count == 3 and got[("www.youtube.com", "connect")].allowed
    assert got[("evil.example", "connect")].allowed is False


def test_the_report_names_what_is_wrong(fakes):
    a = go()
    fetch("www.youtube.com")
    ok = au.format_report(a.snapshot())
    assert "www.youtube.com" in ok and "every one on the allowlist" in ok
    fetch("evil.example")
    bad = au.format_report(a.snapshot())
    assert "NOT ON THE ALLOWLIST" in bad and "evil.example" in bad and "1 host(s) are NOT on the allowlist" in bad
    assert au.format_report([]) == "No outside connections recorded."


def test_egress_mode_setting_is_validated():
    assert load_settings({}).egress_mode == "record"
    assert load_settings({"NEWTUBE_EGRESS_MODE": "enforce"}).egress_mode == "enforce"
    with pytest.raises(ValueError, match="egress_mode"):
        load_settings({"NEWTUBE_EGRESS_MODE": "paranoid"})


# ---- command line -----------------------------------------------------------------------------------

@pytest.fixture
def cli(tmp_path, monkeypatch, capsys):
    from newtube.cli import main
    monkeypatch.setenv("NEWTUBE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("NEWTUBE_CONFIG", str(tmp_path / "none.toml"))

    def run(*argv):
        code = main(["privacy-check", *argv])
        return code, capsys.readouterr().out
    return run


def test_cli_log_with_nothing_recorded(cli):
    code, out = cli("--log")
    assert code == 0 and "Nothing recorded yet" in out


def test_cli_log_exit_code_says_whether_anything_was_off_list(cli, tmp_path):
    c = connect(tmp_path / "data" / "newtube.db"); migrate(c)
    c.execute("INSERT INTO egress_log VALUES ('www.youtube.com',443,'connect',1,0,5,1,2,'1.2.3.4')")
    c.commit()
    code, out = cli("--log")
    assert code == 0 and "every one on the allowlist" in out
    c.execute("INSERT INTO egress_log VALUES ('tracker.example',443,'connect',0,0,1,1,2,'5.6.7.8')")
    c.commit()
    code, out = cli("--log")
    assert code == 1 and "tracker.example" in out
