from pathlib import Path

from newtube.config import load_settings


def test_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = load_settings({})
    assert s.port == 8080
    assert s.db_path == Path("data/newtube.db")


def test_env_overrides_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "newtube.toml").write_text('port = 9000\ncache_max_bytes = 5\n')
    s = load_settings({"NEWTUBE_PORT": "9100"})
    assert s.port == 9100          # env wins
    assert s.cache_max_bytes == 5  # file beats default
