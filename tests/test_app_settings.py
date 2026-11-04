"""Editing settings from the page: validation, where values come from, live vs restart, and the restart button."""
import asyncio
import os
import signal
import time

import pytest

from newtube import settings_registry as reg
from newtube.config import Settings
from newtube.db.connection import connect, migrate
from tests.test_api import env  # noqa: F401


@pytest.fixture(autouse=True)
def no_toml_or_env(monkeypatch, tmp_path):
    """Tests must not see a real newtube.toml, NEWTUBE_* variables, or a systemd marker from the machine they run on."""
    monkeypatch.setenv("NEWTUBE_CONFIG", str(tmp_path / "none.toml"))
    for name in list(os.environ):
        if name.startswith("NEWTUBE_") and name != "NEWTUBE_CONFIG":
            monkeypatch.delenv(name)
    monkeypatch.delenv("INVOCATION_ID", raising=False)


# ---- the registry itself ----------------------------------------------------------------------------------

def test_every_editable_setting_exists_has_a_valid_default_and_a_group():
    defaults = Settings()
    for f in reg.FIELDS:
        assert hasattr(defaults, f.name), f.name
        assert reg.validate(f.name, getattr(defaults, f.name)) == getattr(defaults, f.name), f.name   # the built-in value passes its own rule
        assert f.group in reg.GROUPS and f.label and f.help, f.name
    assert len({f.name for f in reg.FIELDS}) == len(reg.FIELDS)


def test_host_port_and_folders_cannot_be_edited_here():
    for name in ("host", "port", "data_dir", "frontend_dir", "js_runtime", "js_runtime_path", "bogus"):
        with pytest.raises(reg.Invalid):
            reg.validate(name, "x")


@pytest.mark.parametrize("name,raw,expected", [
    ("backfill_daily_cap", 30, 30), ("backfill_daily_cap", "30", 30), ("backfill_daily_cap", 30.0, 30), ("backfill_daily_cap", 0, 0),
    ("min_request_interval", "2.5", 2.5), ("request_jitter", 0, 0.0),
    ("rss_poll_interval", 14400, 14400), ("ip_version", "4", "4"), ("egress_mode", "enforce", "enforce"),
    ("outbound_proxy", " http://127.0.0.1:8888 ", "http://127.0.0.1:8888"), ("outbound_proxy", "socks5h://vpn:1080", "socks5h://vpn:1080"),
    ("outbound_proxy", "", ""), ("embedding_model", "paraphrase-multilingual-MiniLM-L12-v2", "paraphrase-multilingual-MiniLM-L12-v2"),
    ("backup_dir", " /mnt/backups ", "/mnt/backups"),
])
def test_good_values_are_accepted(name, raw, expected):
    assert reg.validate(name, raw) == expected


@pytest.mark.parametrize("name,raw", [
    ("backfill_daily_cap", -1), ("backfill_daily_cap", 99999), ("backfill_daily_cap", 1.5), ("backfill_daily_cap", ""),
    ("backfill_daily_cap", "abc"), ("backfill_daily_cap", None), ("backfill_daily_cap", True), ("backfill_daily_cap", float("nan")),
    ("backfill_daily_cap", float("inf")), ("min_request_interval", 0.01), ("request_jitter", -1), ("rss_poll_interval", 60),
    ("ip_version", "5"), ("egress_mode", "paranoid"), ("embedding_device", "tpu"),
    ("outbound_proxy", "ftp://x:1"), ("outbound_proxy", "just words"), ("outbound_proxy", "http://a b:1"), ("outbound_proxy", "http://"),
    ("embedding_model", "../etc"), ("embedding_model", "a/b"), ("embedding_model", ".."), ("backup_dir", "x" * 400), ("backup_dir", "a\x00b"),
])
def test_bad_values_are_refused_with_a_reason(name, raw):
    with pytest.raises(reg.Invalid) as e:
        reg.validate(name, raw)
    assert e.value.name == name and e.value.message


def test_a_range_error_speaks_in_the_units_you_see():
    with pytest.raises(reg.Invalid, match=r"between 0\.25 hours and 48 hours"):
        reg.validate("rss_poll_interval", 10)


def test_presets_only_contain_valid_editable_values():
    for key, preset in reg.PRESETS.items():
        for name, value in (preset["values"] or {}).items():
            assert reg.validate(name, value) == value, (key, name)


def test_saved_choices_round_trip_and_bad_stored_entries_are_ignored(tmp_path):
    conn = connect(tmp_path / "t.db"); migrate(conn)
    assert reg.load_overrides(conn) == {}
    reg.save_overrides(conn, {"backfill_daily_cap": 5, "egress_mode": "paranoid", "host": "0.0.0.0", "ip_version": "4"})
    assert reg.load_overrides(conn) == {"backfill_daily_cap": 5, "ip_version": "4"}          # invalid / uneditable entries dropped
    conn.execute("UPDATE settings SET value='not json' WHERE key=?", (reg.KEY,))
    assert reg.load_overrides(conn) == {}


def test_saved_choices_apply_but_never_over_an_environment_variable(tmp_path):
    conn = connect(tmp_path / "t.db"); migrate(conn)
    reg.save_overrides(conn, {"backfill_daily_cap": 5, "related_daily_cap": 6})
    s = Settings()
    applied = reg.apply_db_overrides(s, conn, env={"NEWTUBE_RELATED_DAILY_CAP": "99"})
    assert applied == {"backfill_daily_cap": 5} and s.backfill_daily_cap == 5 and s.related_daily_cap == Settings().related_daily_cap


# ---- the server start-up and the command line honour it ------------------------------------------------------------

def test_the_server_starts_with_what_you_saved(tmp_path):
    from newtube.app import build_context
    s = Settings(data_dir=tmp_path)
    conn = connect(s.db_path); migrate(conn)
    reg.save_overrides(conn, {"backfill_daily_cap": 7, "min_request_interval": 3.5, "ip_version": "4"})
    conn.close()
    ctx = build_context(Settings(data_dir=tmp_path))
    assert ctx.settings.backfill_daily_cap == 7 and ctx.limiter.min_interval == 3.5 and ctx.settings.ip_version == "4"
    assert ctx.started_with["backfill_daily_cap"] == 7


def test_commands_see_saved_settings_too(tmp_path, monkeypatch, capsys):
    from newtube.cli import main
    monkeypatch.setenv("NEWTUBE_DATA_DIR", str(tmp_path / "data"))
    conn = connect(tmp_path / "data" / "newtube.db"); migrate(conn)
    reg.save_overrides(conn, {"backfill_daily_cap": 7})
    main(["backfill", "status"])
    assert "of 7 allowed" in capsys.readouterr().out


def test_the_scheduler_picks_up_a_changed_backfill_cap_at_once(tmp_path):
    from newtube.jobs.scheduler import Scheduler
    conn = connect(tmp_path / "t.db"); migrate(conn)
    s = Settings()
    sch = Scheduler(s, conn, http=None, extractor=object())
    s.backfill_daily_cap = 3                                  # changed on the Settings page while running
    sch.related = None
    sch._embed_off = True
    asyncio.run(sch.step())
    assert sch.backfiller.daily_cap == 3


# ---- the API ----------------------------------------------------------------------------------------------------------

def test_get_describes_every_field_and_the_traffic_status(env):
    client, ctx, ex, seen = env
    p = client.get("/api/settings/app").json()
    assert [f["name"] for f in p["fields"]] == [f.name for f in reg.FIELDS]
    assert {g["id"] for g in p["groups"]} == set(reg.GROUPS) and p["can_restart"] is False and p["pending_restart"] is False
    f = next(x for x in p["fields"] if x["name"] == "rss_poll_interval")
    assert (f["scale"], f["unit"], f["live"], f["locked_by_env"], f["saved"]) == (3600, "hours", True, False, None)
    u = p["status"]["usage"]
    assert u["backfill_cap"] == ctx.settings.backfill_daily_cap and {"related_24h", "channel_lookups_hour", "background_today"} <= set(u)
    assert p["status"]["bot_check"]["active"] is False and set(p["presets"]) == {"quiet", "paused", "defaults"}


def test_a_live_setting_takes_effect_the_moment_it_is_saved(env):
    client, ctx, ex, seen = env
    r = client.put("/api/settings/app", json={"values": {"backfill_daily_cap": 5, "min_request_interval": 3.0, "background_daily_cap": 77}})
    assert r.status_code == 200
    assert (ctx.settings.backfill_daily_cap, ctx.limiter.min_interval, ctx.limiter.daily_cap) == (5, 3.0, 77)
    f = {x["name"]: x for x in r.json()["fields"]}
    assert f["backfill_daily_cap"]["value"] == 5 and f["backfill_daily_cap"]["saved"] == 5 and f["backfill_daily_cap"]["pending_restart"] is False
    assert client.get("/api/settings/app").json()["status"]["usage"]["background_cap"] == 77


def test_a_restart_setting_is_saved_but_waits_for_the_restart(env):
    client, ctx, ex, seen = env
    before = ctx.settings.ip_version
    r = client.put("/api/settings/app", json={"values": {"ip_version": "4", "egress_mode": "enforce"}}).json()
    assert ctx.settings.ip_version == before                       # the running server is untouched
    f = {x["name"]: x for x in r["fields"]}
    assert f["ip_version"]["saved"] == "4" and f["ip_version"]["pending_restart"] is True and r["pending_restart"] is True
    assert f["ip_version"]["value"] == before                      # "value" is what is RUNNING


def test_nothing_is_saved_if_any_value_is_wrong(env):
    client, ctx, ex, seen = env
    r = client.put("/api/settings/app", json={"values": {"backfill_daily_cap": 5, "request_jitter": -3}})
    assert r.status_code == 400 and r.json()["detail"]["field"] == "request_jitter" and "Random extra pause" in r.json()["detail"]["message"]
    assert reg.load_overrides(ctx.conn) == {} and ctx.settings.backfill_daily_cap == Settings().backfill_daily_cap


def test_unknown_and_uneditable_names_are_refused(env):
    client, ctx, ex, seen = env
    for name in ("host", "port", "data_dir", "nope"):
        assert client.put("/api/settings/app", json={"values": {name: "x"}}).status_code == 400
    assert client.put("/api/settings/app", json={"values": "x"}).status_code == 400
    assert client.put("/api/settings/app", json={}).status_code == 400


def test_null_removes_your_choice_and_the_file_or_default_applies_again(env):
    client, ctx, ex, seen = env
    client.put("/api/settings/app", json={"values": {"backfill_daily_cap": 5, "min_request_interval": 9.0}})
    r = client.put("/api/settings/app", json={"values": {"backfill_daily_cap": None, "min_request_interval": None}})
    assert r.status_code == 200 and reg.load_overrides(ctx.conn) == {}
    assert ctx.settings.backfill_daily_cap == Settings().backfill_daily_cap
    assert ctx.limiter.min_interval == Settings().min_request_interval                  # the limiter was reset too


def test_a_setting_fixed_by_an_environment_variable_cannot_be_changed(env, monkeypatch):
    client, ctx, ex, seen = env
    monkeypatch.setenv("NEWTUBE_BACKFILL_DAILY_CAP", "5")
    f = next(x for x in client.get("/api/settings/app").json()["fields"] if x["name"] == "backfill_daily_cap")
    assert f["locked_by_env"] is True
    r = client.put("/api/settings/app", json={"values": {"backfill_daily_cap": 9}})
    assert r.status_code == 400 and "NEWTUBE_BACKFILL_DAILY_CAP" in r.json()["detail"]["message"]


def test_the_page_needs_a_login(env):
    from fastapi.testclient import TestClient
    client, ctx, ex, seen = env
    anon = TestClient(client.app)
    assert anon.get("/api/settings/app").status_code == 401
    assert anon.put("/api/settings/app", json={"values": {}}).status_code == 401
    assert anon.post("/api/system/restart").status_code == 401


def test_the_status_shows_a_bot_check(env):
    client, ctx, ex, seen = env
    ctx.extractor = type("E", (), {})()
    from newtube.extract.botgate import BotGate
    ctx.extractor.gate = BotGate()
    ctx.extractor.gate.trip(background=True)
    s = client.get("/api/settings/app").json()["status"]["bot_check"]
    assert s["active"] is True and s["click_retry_seconds"] >= 1 and s["background_paused_minutes"] >= 300


# ---- the restart button ----------------------------------------------------------------------------------------------

def test_restart_is_refused_when_nothing_would_bring_the_server_back(env):
    client, ctx, ex, seen = env
    r = client.post("/api/system/restart")
    assert r.status_code == 400 and "systemd" in r.json()["detail"]


def test_restart_under_systemd_stops_the_process_after_replying(env, monkeypatch):
    from newtube.api import appsettings
    client, ctx, ex, seen = env
    monkeypatch.setenv("INVOCATION_ID", "abc123")
    killed = []
    monkeypatch.setattr(appsettings.os, "kill", lambda pid, sig: killed.append((pid, sig)))
    assert client.get("/api/settings/app").json()["can_restart"] is True
    assert client.post("/api/system/restart").json() == {"restarting": True}
    time.sleep(1.4)
    assert killed == [(os.getpid(), signal.SIGTERM)]
